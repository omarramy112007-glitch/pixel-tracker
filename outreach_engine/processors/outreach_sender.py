# outreach_engine/processors/outreach_sender.py

from __future__ import annotations

import asyncio
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from outreach_engine.core.lead_manager import get_lead
from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase
from outreach_engine.core.performance_logger import timer
from outreach_engine.utils.logger import get_logger
from outreach_engine.core.gmail_sender import send_via_gmail, GmailRateLimitError

from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.processors.follow_up_manager import (
    decide_followup_action,
    get_followup_email_content,
    mark_lead_failed,
    mark_lead_replied,
    update_followup_sent,
)

logger = get_logger(__name__)

SENDER_NAME        = os.getenv("SENDER_NAME", "").strip()
REPLY_TO           = os.getenv("REPLY_TO", "").strip() or None
FOLLOWUP_GAP_HOURS = 24

PUBLIC_TRACKING_BASE_URL = (
    os.getenv("PUBLIC_TRACKING_BASE_URL")
    or os.getenv("TRACKING_BASE_URL")
    or os.getenv("PIXEL_BASE_URL")
    or "https://YOUR_PUBLIC_DOMAIN"
).rstrip("/")

CTA_DESTINATION_URL = os.getenv(
    "CTA_DESTINATION_URL", "https://your-landing-page.com"
).strip()

# Matches ANY existing tracking pixel so we can strip before injecting
PIXEL_TAG_RE = re.compile(
    r'<img[^>]+src=["\'][^"\']*/open/[^"\']*["\'][^>]*/?>',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _normalize(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _lead_name(lead: Dict[str, Any]) -> str:
    first = (lead.get("first_name") or "").strip()
    last  = (lead.get("last_name") or "").strip()
    return " ".join(filter(None, [first, last])).strip() or "there"


def _is_initial(lead: Dict[str, Any]) -> bool:
    return (
        _normalize(lead.get("status")) in {"new", "pending", "not_contacted", ""}
        and lead.get("last_email_sent") is None
    )


def _is_terminal(lead: Dict[str, Any]) -> bool:
    status          = _normalize(lead.get("status"))
    followup_status = _normalize(lead.get("followup_status") or "")
    return status in {
        "failed", "replied", "completed", "converted",
        "won", "lost", "closed", "opt-out", "cancelled",
    } or followup_status in {"completed", "failed"}


def _passes_quality(lead: Dict[str, Any]) -> bool:
    return bool(lead.get("email") and lead.get("company"))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _set_fields(lead_id: int, data: Dict[str, Any]) -> None:
    try:
        supabase.table("outreach_leads").update(data).eq("id", lead_id).execute()
    except Exception as e:
        logger.warning(f"DB update failed lead_id={lead_id}: {e}")


def _mark_sent_cold(
    lead_id: int,
    thread_id: Optional[str],
    gmail_msg_id: Optional[str],
    sent_at: datetime,
) -> None:
    payload: Dict[str, Any] = {
        "status":              "sent",
        "sent_email_type":     "cold",
        "last_email_sent":     sent_at.isoformat(),
        "last_contacted":      sent_at.isoformat(),
        "last_updated":        _now_utc().isoformat(),
        "next_followup":       (sent_at + timedelta(hours=FOLLOWUP_GAP_HOURS)).isoformat(),
        "followup_step":       0,
        "followup_status":     None,
        "open_count":          0,
        "followup_open_count": 0,
        "reply_count":         0,
        "email_opened":        False,
        "link_clicked":        False,
    }
    if thread_id:
        payload["thread_id"] = thread_id
    if gmail_msg_id:
        payload["gmail_message_id"] = gmail_msg_id
    _set_fields(lead_id, payload)


def _mark_rate_limited(lead_id: int) -> None:
    _set_fields(lead_id, {
        "status":        "rate_limited",
        "next_followup": (_now_utc() + timedelta(hours=24)).isoformat(),
        "last_updated":  _now_utc().isoformat(),
    })


def _mark_failed_send(lead_id: int) -> None:
    _set_fields(lead_id, {
        "status":       "failed",
        "last_updated": _now_utc().isoformat(),
    })


# ---------------------------------------------------------------------------
# Pixel URL builder
# ---------------------------------------------------------------------------

def _build_pixel_url(
    lead_id: int,
    campaign_id: int,
    email_type: str,
    send_ts: int,
) -> str:
    """
    Unique pixel URL per send.
    token = cryptographically random — prevents Google prefetch from
    firing old cached pixels when a new email arrives.
    """
    token = secrets.token_hex(8)
    return (
        f"{PUBLIC_TRACKING_BASE_URL}/open/{lead_id}"
        f"?campaign_id={campaign_id}"
        f"&email_type={email_type}"
        f"&ts={send_ts}"
        f"&t={token}"
    )


def _build_cta_url(
    lead_id: int,
    campaign_id: int,
    email_type: str,
) -> str:
    return (
        f"{PUBLIC_TRACKING_BASE_URL}/click/{lead_id}"
        f"?campaign_id={campaign_id}"
        f"&email_type={email_type}"
        f"&url={quote(CTA_DESTINATION_URL, safe='')}"
    )


# ---------------------------------------------------------------------------
# Pixel injection — single source of truth
# ---------------------------------------------------------------------------

def _inject_pixel(html_body: str, pixel_url: str) -> str:
    """
    Strip ALL existing tracking pixels then inject exactly one.
    This is the ONLY place pixels are injected.
    gmail_sender must NOT inject pixels — it receives html_body
    with the pixel already embedded and passes tracking_pixel_url=None.
    """
    cleaned   = PIXEL_TAG_RE.sub("", html_body)
    pixel_tag = (
        f'<img src="{pixel_url}" width="1" height="1" '
        f'style="display:none;opacity:0;position:absolute;" alt="" />'
    )
    idx = cleaned.lower().rfind("</body>")
    if idx != -1:
        return cleaned[:idx] + pixel_tag + cleaned[idx:]
    return cleaned + pixel_tag


# ---------------------------------------------------------------------------
# Email builders
# ---------------------------------------------------------------------------

def _build_cold_email(
    lead: Dict[str, Any],
    campaign_id: int,
    send_ts: int,
) -> Dict[str, Any]:
    lead_id   = int(lead["id"])
    pixel_url = _build_pixel_url(lead_id, campaign_id, "cold", send_ts)
    cta_url   = _build_cta_url(lead_id, campaign_id, "cold")

    result = personalize_email(
        {**lead, "cta_url": cta_url, "sender_name": SENDER_NAME},
        step=0,
        use_dynamic_subject=True,
    )

    subject   = (result.get("subject") or "").strip()
    body      = (result.get("body") or "").strip()
    html_body = (result.get("html_body") or "").strip() or body.replace("\n", "<br>")

    if not subject or not body:
        return {}

    # Inject pixel here — gmail_sender will receive tracking_pixel_url=None
    html_body = _inject_pixel(html_body, pixel_url)

    return {
        "subject":    subject,
        "body":       body,
        "html_body":  html_body,
        "pixel_url":  pixel_url,
        "email_type": "cold",
    }


def _build_followup_email(
    lead: Dict[str, Any],
    campaign_id: int,
    action: str,
    send_ts: int,
) -> Dict[str, Any]:
    lead_id   = int(lead["id"])
    pixel_url = _build_pixel_url(lead_id, campaign_id, "followup", send_ts)
    cta_url   = _build_cta_url(lead_id, campaign_id, "followup")

    content   = get_followup_email_content(action, lead)
    subject   = (content.get("subject") or "").strip()
    body      = (content.get("body") or "").strip()
    html_body = (content.get("html_body") or "").strip()

    if not subject or not body:
        return {}

    class _Safe(dict):
        def __missing__(self, key):
            return ""

    ctx = _Safe({
        "name":        _lead_name(lead),
        "company":     lead.get("company") or "",
        "pain_hook":   lead.get("pain_points") or "low reply rates",
        "cta_url":     cta_url,
        "sender_name": SENDER_NAME,
    })

    subject   = subject.format_map(ctx).strip()
    body      = body.format_map(ctx).strip()
    html_body = (
        html_body.format_map(ctx).strip()
        if html_body
        else body.replace("\n", "<br>")
    )

    # Inject pixel here — gmail_sender will receive tracking_pixel_url=None
    html_body = _inject_pixel(html_body, pixel_url)

    return {
        "subject":    subject,
        "body":       body,
        "html_body":  html_body,
        "pixel_url":  pixel_url,
        "email_type": "followup",
    }


# ---------------------------------------------------------------------------
# Send — pixel already in html_body, pass tracking_pixel_url=None
# ---------------------------------------------------------------------------

def _gmail_send(
    to_email: str,
    subject: str,
    body: str,
    html_body: str,
) -> Any:
    """
    Pass tracking_pixel_url=None because the pixel is already
    embedded in html_body by _inject_pixel().
    Passing it again causes gmail_sender to inject a second pixel.
    thread_id=None always — new thread per email prevents Gmail
    from loading all thread images when any one email is opened.
    """
    return send_via_gmail(
        to_email=to_email,
        subject=subject,
        body=body,
        tracking_pixel_url=None,   # pixel already in html_body
        reply_to=REPLY_TO,
        html_body=html_body,
        thread_id=None,            # always new thread
    )


@timer("send_times")
def send_email_sync(
    lead_email: str,
    campaign_id: int,
    initial_outreach: bool = False,
    **kwargs,
) -> bool:
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return False
    if _is_terminal(lead):
        return False
    if not _passes_quality(lead):
        return False

    lead_id = int(lead["id"])
    status  = _normalize(lead.get("status"))

    # ── Cold email ────────────────────────────────────────────────────────
    if initial_outreach or _is_initial(lead):
        if not _is_initial(lead):
            return False

        send_ts       = int(_now_utc().timestamp())
        email_content = _build_cold_email(lead, campaign_id, send_ts)
        if not email_content:
            return False

        try:
            result = _gmail_send(
                to_email=lead_email,
                subject=email_content["subject"],
                body=email_content["body"],
                html_body=email_content["html_body"],
            )
            if not result:
                raise RuntimeError("no result from gmail")
        except GmailRateLimitError:
            _mark_rate_limited(lead_id)
            return False
        except Exception as e:
            if "429" in str(e):
                _mark_rate_limited(lead_id)
            else:
                _mark_failed_send(lead_id)
            logger.error(f"❌ Cold send failed → {lead_email}: {e}")
            return False

        thread_id    = result.get("thread_id") if isinstance(result, dict) else None
        gmail_msg_id = result.get("message_id") if isinstance(result, dict) else None
        _mark_sent_cold(lead_id, thread_id, gmail_msg_id, sent_at=_now_utc())

        store_event(
            lead_id=lead_id, campaign_id=campaign_id,
            event_type="sent",
            metadata={
                "email_type":       "cold",
                "channel":          "email",
                "thread_id":        thread_id,
                "gmail_message_id": gmail_msg_id,
            },
        )
        logger.info(f"✅ Cold sent → {lead_email}")
        return True

    # ── Follow-up ─────────────────────────────────────────────────────────
    if status != "sent":
        return False

    action = decide_followup_action(lead)
    if not action:
        return False
    if action == "__mark_failed__":
        mark_lead_failed(lead_email, campaign_id)
        return False
    if action in ("__mark_replied__", "__mark_completed__"):
        mark_lead_replied(lead_email, campaign_id)
        return False

    send_ts       = int(_now_utc().timestamp())
    email_content = _build_followup_email(lead, campaign_id, action, send_ts)
    if not email_content:
        mark_lead_failed(lead_email, campaign_id)
        return False

    try:
        result = _gmail_send(
            to_email=lead_email,
            subject=email_content["subject"],
            body=email_content["body"],
            html_body=email_content["html_body"],
        )
        if not result:
            raise RuntimeError("no result from gmail")
    except GmailRateLimitError:
        _mark_rate_limited(lead_id)
        return False
    except Exception as e:
        if "429" in str(e):
            _mark_rate_limited(lead_id)
        else:
            _mark_failed_send(lead_id)
        logger.error(f"❌ Followup send failed → {lead_email}: {e}")
        return False

    gmail_msg_id = result.get("message_id") if isinstance(result, dict) else None

    update_followup_sent(
        lead_email=lead_email,
        campaign_id=campaign_id,
        action=action,
        gmail_message_id=gmail_msg_id,
    )

    store_event(
        lead_id=lead_id, campaign_id=campaign_id,
        event_type="sent",
        metadata={
            "email_type":       action,
            "channel":          "email",
            "gmail_message_id": gmail_msg_id,
        },
    )
    logger.info(f"✅ Followup sent → {lead_email} ({action})")
    return True


async def send_email_async(*args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, partial(send_email_sync, *args, **kwargs)
    )


async def send_bulk_emails(
    leads: List[dict], concurrency: int = 10, **kwargs
) -> List[bool]:
    initial_outreach = bool(kwargs.pop("initial_outreach", False))
    seen: set        = set()
    eligible: List[Dict[str, Any]] = []

    for lead in leads:
        email       = lead.get("email")
        campaign_id = lead.get("campaign_id")
        if not email or campaign_id is None:
            continue
        key = (email.strip().lower(), int(campaign_id))
        if key in seen:
            continue
        db = get_lead(email, campaign_id)
        if not db or _is_terminal(db):
            continue
        if initial_outreach and not _is_initial(db):
            continue
        eligible.append(db)
        seen.add(key)

    if not eligible:
        return []

    sem = asyncio.Semaphore(concurrency)

    async def worker(lead: Dict[str, Any]) -> bool:
        async with sem:
            try:
                return await send_email_async(
                    lead["email"],
                    lead["campaign_id"],
                    initial_outreach=initial_outreach,
                )
            except Exception as e:
                logger.error(f"❌ Worker failed → {lead.get('email')}: {e}")
                return False

    results = await asyncio.gather(*[worker(l) for l in eligible])
    print(f"📨 Sent {sum(r is True for r in results)}/{len(eligible)}")
    return list(results)
