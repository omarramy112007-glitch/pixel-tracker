# outreach_engine/processors/outreach_sender.py

from __future__ import annotations

import asyncio
import os
import random
import re
from datetime import datetime, timedelta, timezone
from functools import partial
from html import escape
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from outreach_engine.core.proxy_rotator import get_next_proxy
from outreach_engine.processors.follow_up_manager import determine_next_step
from outreach_engine.core.lead_manager import get_lead
from outreach_engine.database.event_repository import store_event
from outreach_engine.core.performance_logger import timer
from outreach_engine.utils.logger import get_logger
from outreach_engine.core.templates import TEMPLATES
from outreach_engine.core.gmail_sender import send_via_gmail, GmailRateLimitError

logger = get_logger(__name__)

TEST_EMAIL = os.getenv("TEST_EMAIL", "").strip().lower()

MIN_SEND_DELAY_SECONDS = max(1, int(os.getenv("MIN_SEND_DELAY_SECONDS", "3")))
MAX_SEND_DELAY_SECONDS = max(
    MIN_SEND_DELAY_SECONDS,
    int(os.getenv("MAX_SEND_DELAY_SECONDS", "8")),
)

RESEND_COOLDOWN_HOURS = max(1, int(os.getenv("RESEND_COOLDOWN_HOURS", "12")))

# 0 or negative = unlimited
MAX_SENDS_PER_RUN = int(os.getenv("MAX_SENDS_PER_RUN", "0"))

SENDER_NAME = os.getenv("SENDER_NAME", "Your Name").strip()
REPLY_TO = os.getenv("REPLY_TO", "").strip() or None

CTA_DESTINATION_URL = os.getenv("CTA_DESTINATION_URL", "").strip()

PIXEL_BASE_URL = os.getenv("PIXEL_BASE_URL", "").strip().rstrip("/")
CLICK_TRACK_BASE_URL = os.getenv("CLICK_TRACK_BASE_URL", "").strip().rstrip("/")

SEND_LOCK = asyncio.Lock()


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def _normalize_text(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _lead_name(lead: Dict[str, Any]) -> str:
    first = (lead.get("first_name") or "").strip()
    last = (lead.get("last_name") or "").strip()
    name = " ".join(filter(None, [first, last])).strip()
    if name:
        return name
    return (lead.get("name") or lead.get("person_name") or "").strip()


def _passes_minimum_quality(lead: Dict[str, Any]) -> bool:
    return bool(lead.get("email") and lead.get("company") and _lead_name(lead))


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _cooldown_passed(lead: Dict[str, Any]) -> bool:
    last = _parse_dt(lead.get("last_email_sent"))
    if not last:
        return True

    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    return _now_utc() - last > timedelta(hours=RESEND_COOLDOWN_HOURS)


def _next_followup_due(lead: Dict[str, Any]) -> bool:
    nxt = _parse_dt(lead.get("next_followup"))
    if not nxt:
        return True

    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)

    return _now_utc() >= nxt


def _set_lead_fields(lead_id: int, data: Dict[str, Any]) -> None:
    try:
        from outreach_engine.database.supabase_client import supabase
        supabase.table("outreach_leads").update(data).eq("id", lead_id).execute()
    except Exception as e:
        logger.warning(f"Lead update failed for lead_id={lead_id}: {e}")


def _merge_lead_metadata(lead_id: int, extra: Dict[str, Any]) -> None:
    try:
        from outreach_engine.database.supabase_client import supabase
        res = (
            supabase.table("outreach_leads")
            .select("metadata")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        current = {}
        if res.data:
            current = res.data[0].get("metadata") or {}
            if not isinstance(current, dict):
                current = {}
        current.update(extra)
        _set_lead_fields(
            lead_id,
            {
                "metadata": current,
                "last_updated": _now_utc().isoformat(),
            },
        )
    except Exception:
        pass


def _mark_processing(lead_id: int, step: int) -> None:
    now = _now_utc().isoformat()
    _set_lead_fields(
        lead_id,
        {
            "status": "processing",
            "followup_step": step,
            "last_updated": now,
        },
    )


def _mark_sent(
    lead_id: int,
    step: int,
    thread_id: Optional[str] = None,
    gmail_message_id: Optional[str] = None,
) -> None:
    now = _now_utc().isoformat()
    payload: Dict[str, Any] = {
        "status": "sent",
        "followup_step": step,
        "last_email_sent": now,
        "last_updated": now,
    }

    if thread_id:
        payload["thread_id"] = thread_id

    if gmail_message_id:
        payload["gmail_message_id"] = gmail_message_id

    _set_lead_fields(lead_id, payload)


def _mark_failed(lead_id: int, reason: Optional[str] = None) -> None:
    payload: Dict[str, Any] = {
        "status": "failed",
        "last_updated": _now_utc().isoformat(),
    }
    _set_lead_fields(lead_id, payload)

    if reason:
        _merge_lead_metadata(lead_id, {"last_failure": reason[:500]})


def _mark_rate_limited(lead_id: int, retry_after: Optional[datetime] = None) -> None:
    now = _now_utc()
    next_try = retry_after or (now + timedelta(hours=24))
    payload: Dict[str, Any] = {
        "status": "rate_limited",
        "next_followup": next_try.isoformat(),
        "last_updated": now.isoformat(),
    }
    _set_lead_fields(lead_id, payload)


def _should_send_initial(lead: Dict[str, Any]) -> bool:
    return _normalize_text(lead.get("status")) in {"new", "pending", "not_contacted", ""}


def _should_send_followup(lead: Dict[str, Any], next_step: int) -> bool:
    status = _normalize_text(lead.get("status"))
    current_step = int(lead.get("followup_step") or 0)

    if status not in {"sent", "rate_limited"}:
        return False
    if not _next_followup_due(lead):
        return False
    if next_step == -1:
        return False
    if next_step <= current_step:
        return False

    return True


def _choose_template_name(lead: Dict[str, Any], step: int) -> str:
    industry = _normalize_text(lead.get("industry"))

    if step == 0:
        if "saas" in industry:
            return "cold_email_saas"
        if any(x in industry for x in ("ecommerce", "commerce", "retail")):
            return "cold_email_ecommerce"
        return "cold_email"

    if step == 1:
        return "followup_1"
    if step == 2:
        return "followup_2"
    if step == 3:
        return "followup_3"

    return "value_add"


def _render_template_safe(template_name: str, context: Dict[str, Any]) -> Dict[str, str]:
    template = TEMPLATES.get(template_name) or TEMPLATES.get("cold_email") or {}

    safe_context = _SafeDict(
        {k: ("" if v is None else v) for k, v in context.items()}
    )

    def _fmt(value: str) -> str:
        try:
            return (value or "").format_map(safe_context).strip()
        except Exception:
            return (value or "").strip()

    subject = _fmt(template.get("subject", ""))
    body = _fmt(template.get("body", ""))
    html_body = _fmt(template.get("html_body", ""))

    return {
        "subject": subject,
        "body": body,
        "html_body": html_body,
    }


def _is_public_http_url(url: Optional[str]) -> bool:
    if not url:
        return False

    clean = url.strip().lower()
    if not clean.startswith(("http://", "https://")):
        return False

    if "localhost" in clean or "127.0.0.1" in clean:
        return False

    return True


def _resolve_tracking_urls(
    lead: Dict[str, Any],
    campaign_id: int,
) -> Tuple[Optional[str], Optional[str]]:
    lead_id = lead.get("id")

    open_url = lead.get("open_tracking_url")
    click_url = lead.get("click_tracking_url")

    if not open_url and PIXEL_BASE_URL and lead_id:
        open_url = f"{PIXEL_BASE_URL}/open/{lead_id}?campaign_id={campaign_id}"

    if not click_url and CLICK_TRACK_BASE_URL and lead_id:
        visible_target = (
            lead.get("visible_cta_url")
            or CTA_DESTINATION_URL
            or lead.get("website")
            or (lead.get("raw") or {}).get("website")
        )

        if _is_public_http_url(visible_target):
            dest = quote(visible_target, safe="")
            click_url = (
                f"{CLICK_TRACK_BASE_URL}/click/{lead_id}"
                f"?campaign_id={campaign_id}&url={dest}"
            )
        else:
            click_url = f"{CLICK_TRACK_BASE_URL}/click/{lead_id}?campaign_id={campaign_id}"

    return open_url, click_url


def _build_html_from_text(
    body: str,
    sender_name: str,
    click_url: Optional[str],
    open_url: Optional[str],
) -> str:
    safe_lines = []
    for line in (body or "").splitlines():
        line = line.rstrip()
        if not line.strip():
            safe_lines.append("<br>")
        else:
            safe_lines.append(f"<p style='margin:0 0 10px 0;'>{escape(line)}</p>")

    cta_html = ""
    if click_url:
        cta_html = f"""
        <p style="margin-top: 18px;">
          <a href="{escape(click_url, quote=True)}" style="display:inline-block;padding:12px 18px;border-radius:10px;background:#111827;color:#ffffff;text-decoration:none;">
            View details
          </a>
        </p>
        """

    pixel_html = ""
    if open_url:
        pixel_html = f"""
        <img src="{escape(open_url, quote=True)}" width="1" height="1" style="display:none !important; width:1px; height:1px; opacity:0; visibility:hidden;" alt="" />
        """

    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #111827;">
        {''.join(safe_lines)}
        {cta_html}
        <p style="margin-top: 18px;">Best,<br>{escape(sender_name)}</p>
        {pixel_html}
      </body>
    </html>
    """


def _extract_send_metadata(send_result: Any) -> Tuple[Optional[str], Optional[str]]:
    if isinstance(send_result, dict):
        thread_id = send_result.get("thread_id") or send_result.get("gmail_thread_id")
        message_id = send_result.get("message_id") or send_result.get("gmail_message_id")
        return (
            str(thread_id) if thread_id else None,
            str(message_id) if message_id else None,
        )

    if isinstance(send_result, (tuple, list)) and len(send_result) >= 2:
        thread_id = send_result[0]
        message_id = send_result[1]
        return (
            str(thread_id) if thread_id else None,
            str(message_id) if message_id else None,
        )

    return None, None


def _build_email_payload(
    lead: Dict[str, Any],
    campaign_id: int,
    step: int,
) -> Dict[str, Any]:
    lead_id = int(lead.get("id"))
    sender_name = lead.get("sender_name") or SENDER_NAME
    template_name = _choose_template_name(lead, step)

    open_url, click_url = _resolve_tracking_urls(lead, campaign_id)

    context = {
        **lead,
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "sender_name": sender_name,
        "cta_text": "Click here to learn more.",
        "cta_url": click_url or "",
        "tracking_pixel_url": open_url or "",
        "dynamic_offer": lead.get("dynamic_offer") or "our automated outreach system",
        "pain_hook": lead.get("pain_hook") or "low reply rates",
        "name": _lead_name(lead) or "there",
        "company": lead.get("company") or "",
        "industry": lead.get("industry") or "",
        "title": lead.get("title") or "",
        "first_line": lead.get("first_line") or "",
        "website_summary": lead.get("website_summary") or "",
        "visible_cta_url": lead.get("visible_cta_url") or "",
    }

    rendered = _render_template_safe(template_name, context)

    subject = (rendered.get("subject") or "").strip()
    body = (rendered.get("body") or "").strip()
    html_body = (rendered.get("html_body") or "").strip()

    for bad in (
        "http://localhost",
        "https://localhost",
        "http://127.0.0.1",
        "https://127.0.0.1",
    ):
        subject = subject.replace(bad, "")
        body = body.replace(bad, "")
        html_body = html_body.replace(bad, "")

    if not html_body:
        html_body = _build_html_from_text(
            body=body,
            sender_name=sender_name,
            click_url=click_url,
            open_url=open_url,
        )

    return {
        "subject": subject,
        "body": body,
        "html_body": html_body,
        "lead_id": lead_id,
        "open_url": open_url,
        "click_url": click_url,
    }


def _send_html_email(
    to_email: str,
    subject: str,
    body: str,
    html_body: str,
    tracking_pixel_url: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Any:
    return send_via_gmail(
        to_email=to_email,
        subject=subject,
        body=body,
        tracking_pixel_url=tracking_pixel_url,
        reply_to=REPLY_TO,
        html_body=html_body,
        thread_id=thread_id,
    )


def _extract_retry_after(exc: Exception) -> Optional[datetime]:
    retry_seconds = None

    if hasattr(exc, "retry_after_seconds"):
        try:
            retry_seconds = int(getattr(exc, "retry_after_seconds"))
        except Exception:
            retry_seconds = None

    if retry_seconds and retry_seconds > 0:
        return _now_utc() + timedelta(seconds=retry_seconds)

    text = str(exc)
    match = re.search(r"Retry after\s+([0-9T:\.\-]+Z)", text)
    if match:
        try:
            val = match.group(1).replace("Z", "+00:00")
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    return None


@timer("send_times")
def send_email_sync(
    lead_email: str,
    campaign_id: int,
    initial_outreach: bool = False,
    test_mode_active: Optional[bool] = None,
) -> bool:
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return False

    lead_id = int(lead.get("id"))
    status = _normalize_text(lead.get("status"))

    if status in {"processing", "replied", "converted", "opt-out", "failed"}:
        return False

    if status == "rate_limited" and not _next_followup_due(lead):
        return False

    if not _passes_minimum_quality(lead):
        return False

    if status in {"new", "pending", "not_contacted", ""}:
        if not _cooldown_passed(lead):
            return False
        step = 0
        can_send = _should_send_initial(lead)
    else:
        next_step = determine_next_step(lead_email, campaign_id)
        step = next_step
        can_send = _should_send_followup(lead, next_step)

    if not can_send:
        return False

    email = _build_email_payload(
        lead=lead,
        campaign_id=campaign_id,
        step=step,
    )

    if not email["subject"] or not email["body"] or not email["html_body"]:
        return False

    _mark_processing(lead_id, step)

    try:
        proxy = get_next_proxy()
        if proxy:
            logger.info(f"Using proxy: {proxy}")

        send_result = _send_html_email(
            to_email=lead_email,
            subject=email["subject"],
            body=email["body"],
            html_body=email["html_body"],
            tracking_pixel_url=email.get("open_url"),
            thread_id=(lead.get("thread_id") or None),
        )

        if not send_result:
            raise RuntimeError("send_via_gmail returned no result")

    except GmailRateLimitError as e:
        retry_after = _extract_retry_after(e)
        _mark_rate_limited(lead_id, retry_after=retry_after)
        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="rate_limited",
            metadata={
                "error": str(e),
                "retry_after": retry_after.isoformat() if retry_after else None,
                "provider": "gmail",
                "step": step,
                "channel": "email",
            },
        )
        logger.error(f"⚠ Gmail rate limited → {lead_email}: {e}")
        return False

    except Exception as e:
        if "429" in str(e) or "rate limit" in str(e).lower():
            retry_after = _extract_retry_after(e)
            _mark_rate_limited(lead_id, retry_after=retry_after)
            store_event(
                lead_id=lead_id,
                campaign_id=campaign_id,
                event_type="rate_limited",
                metadata={
                    "error": str(e),
                    "retry_after": retry_after.isoformat() if retry_after else None,
                    "provider": "gmail",
                    "step": step,
                    "channel": "email",
                },
            )
            logger.error(f"⚠ Gmail rate limited → {lead_email}: {e}")
            return False

        _mark_failed(lead_id, reason=str(e))
        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="failed",
            metadata={"error": str(e), "provider": "gmail", "step": step, "channel": "email"},
        )
        logger.error(f"❌ Failed → {lead_email}: {e}")
        return False

    thread_id, gmail_message_id = _extract_send_metadata(send_result)

    _mark_sent(
        lead_id,
        step,
        thread_id=thread_id,
        gmail_message_id=gmail_message_id,
    )

    store_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="sent",
        metadata={
            "provider": "gmail",
            "step": step,
            "thread_id": thread_id,
            "gmail_message_id": gmail_message_id,
            "channel": "email",
            "open_url": email.get("open_url"),
            "click_url": email.get("click_url"),
        },
    )

    logger.info(f"✅ Sent → {lead_email} (step {step})")
    return True


async def send_email_async(*args, **kwargs):
    loop = asyncio.get_running_loop()
    fn = partial(send_email_sync, *args, **kwargs)
    return await loop.run_in_executor(None, fn)


async def send_bulk_emails(leads: List[dict], concurrency: int = 10, **kwargs):
    initial_outreach = bool(kwargs.pop("initial_outreach", False))

    filtered = []
    seen = set()

    for l in leads:
        email = l.get("email")
        campaign_id = l.get("campaign_id")
        if not email or campaign_id is None:
            continue

        key = (email.strip().lower(), int(campaign_id))
        if key in seen:
            continue

        db = get_lead(email, campaign_id)
        if not db:
            continue

        db_status = _normalize_text(db.get("status"))

        if initial_outreach:
            if db_status in {"sent", "processing", "replied", "converted", "failed", "opt-out"}:
                continue
        else:
            if db_status in {"processing", "replied", "converted", "failed", "opt-out"}:
                continue
            if db_status == "rate_limited" and not _next_followup_due(db):
                continue

        filtered.append(db)
        seen.add(key)

    if not filtered:
        return []

    if MAX_SENDS_PER_RUN > 0:
        filtered = filtered[:MAX_SENDS_PER_RUN]

    results: List[bool] = []

    for lead in filtered:
        async with SEND_LOCK:
            delay = random.randint(MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS)
            if delay > 0:
                await asyncio.sleep(delay)

            try:
                result = await send_email_async(
                    lead["email"],
                    lead["campaign_id"],
                    initial_outreach=initial_outreach,
                    **kwargs,
                )
                results.append(bool(result))
            except Exception as e:
                logger.error(f"❌ Unexpected send failure → {lead.get('email')}: {e}")
                results.append(False)

    return results