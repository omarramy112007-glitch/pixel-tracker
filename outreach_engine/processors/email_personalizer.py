# outreach_engine/processors/outreach_sender.py

from __future__ import annotations

import asyncio
import os
import random
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

# Cold outreach uses the personalizer
from outreach_engine.processors.email_personalizer import personalize_email

# Follow-up path uses the state machine
from outreach_engine.processors.follow_up_manager import (
    decide_followup_action,
    get_followup_email_content,
    mark_lead_failed,
    mark_lead_completed,
    update_followup_sent,
    determine_next_step,  # legacy shim for bulk_send eligibility check
)

logger = get_logger(__name__)

TEST_EMAIL             = os.getenv("TEST_EMAIL", "").strip().lower()
MIN_SEND_DELAY_SECONDS = int(os.getenv("MIN_SEND_DELAY_SECONDS", "0"))
MAX_SEND_DELAY_SECONDS = int(os.getenv("MAX_SEND_DELAY_SECONDS", "0"))
RESEND_COOLDOWN_HOURS  = 12
SENDER_NAME            = os.getenv("SENDER_NAME", "Omar Ramy").strip()
REPLY_TO               = os.getenv("REPLY_TO", "").strip() or None

PUBLIC_TRACKING_BASE_URL = (
    os.getenv("PUBLIC_TRACKING_BASE_URL")
    or os.getenv("TRACKING_BASE_URL")
    or os.getenv("PIXEL_BASE_URL")
    or "https://YOUR_PUBLIC_DOMAIN"
).rstrip("/")

CTA_DESTINATION_URL = os.getenv("CTA_DESTINATION_URL", "https://your-landing-page.com").strip()

SEND_LOCK = asyncio.Lock()


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def _normalize_text(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _lead_name(lead: Dict[str, Any]) -> str:
    first = (lead.get("first_name") or "").strip()
    last  = (lead.get("last_name") or "").strip()
    name  = " ".join(filter(None, [first, last])).strip()
    return name or (lead.get("name") or lead.get("person_name") or "").strip()


def _passes_minimum_quality(lead: Dict[str, Any]) -> bool:
    """Cold outreach requires email + company + name. Follow-ups only need email."""
    return bool(lead.get("email") and lead.get("company"))


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        raw = str(value).strip().replace("Z", "+00:00")
        dt  = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _cooldown_passed(lead: Dict[str, Any]) -> bool:
    last = _parse_dt(lead.get("last_email_sent"))
    if not last:
        return True
    return _now_utc() - last > timedelta(hours=RESEND_COOLDOWN_HOURS)


def _next_followup_due(lead: Dict[str, Any]) -> bool:
    nxt = _parse_dt(lead.get("next_followup"))
    if not nxt:
        return True
    return _now_utc() >= nxt


# ── DB helpers ────────────────────────────────────────────────────────────────

def _set_lead_fields(lead_id: int, data: Dict[str, Any]) -> None:
    try:
        supabase.table("outreach_leads").update(data).eq("id", lead_id).execute()
    except Exception as e:
        logger.warning(f"Lead update failed for lead_id={lead_id}: {e}")


def _mark_processing(lead_id: int) -> None:
    _set_lead_fields(lead_id, {
        "status":       "processing",
        "last_updated": _now_utc().isoformat(),
    })


def _mark_sent_initial(lead_id: int, thread_id: Optional[str], gmail_msg_id: Optional[str]) -> None:
    """Mark as sent after cold email — schedule first follow-up window."""
    now = _now_utc()
    payload: Dict[str, Any] = {
        "status":          "sent",
        "last_email_sent": now.isoformat(),
        "last_contacted":  now.isoformat(),
        "last_updated":    now.isoformat(),
        # Schedule follow-up check in 48h
        "next_followup":   (now + timedelta(hours=48)).isoformat(),
        "followup_step":   0,
        "followup_status": None,  # cleared — fresh lead entering the follow-up state machine
    }
    if thread_id:
        payload["thread_id"] = thread_id
    if gmail_msg_id:
        payload["gmail_message_id"] = gmail_msg_id
    _set_lead_fields(lead_id, payload)


def _mark_failed_send(lead_id: int, reason: Optional[str] = None) -> None:
    payload: Dict[str, Any] = {
        "status":       "failed",
        "last_updated": _now_utc().isoformat(),
    }
    _set_lead_fields(lead_id, payload)
    if reason:
        try:
            res = supabase.table("outreach_leads").select("metadata").eq("id", lead_id).limit(1).execute()
            existing = (res.data or [{}])[0].get("metadata") or {}
            if isinstance(existing, dict):
                existing["last_failure"] = reason[:500]
                _set_lead_fields(lead_id, {"metadata": existing})
        except Exception:
            pass


def _mark_rate_limited(lead_id: int, retry_after: Optional[datetime] = None) -> None:
    next_try = retry_after or (_now_utc() + timedelta(hours=24))
    _set_lead_fields(lead_id, {
        "status":        "rate_limited",
        "next_followup": next_try.isoformat(),
        "last_updated":  _now_utc().isoformat(),
    })


# ── Send decision helpers ─────────────────────────────────────────────────────

def _is_initial_lead(lead: Dict[str, Any]) -> bool:
    """
    A lead is eligible for initial cold outreach only if status == 'new'.
    pending / not_contacted are also acceptable as aliases.
    """
    status          = _normalize_text(lead.get("status"))
    last_email_sent = lead.get("last_email_sent")
    return (
        (status in {"new", "pending", "not_contacted", ""})
        and (last_email_sent is None)
    )


def _is_terminal(lead: Dict[str, Any]) -> bool:
    status          = _normalize_text(lead.get("status"))
    followup_status = _normalize_text(lead.get("followup_status") or "")
    reply_count     = _safe_int(lead.get("reply_count"))
    reply_status    = lead.get("reply_status")

    if isinstance(reply_status, bool):
        replied = reply_status
    elif isinstance(reply_status, str):
        replied = reply_status.strip().lower() in {"1", "true", "yes", "replied"}
    else:
        replied = False

    terminal_statuses = {
        "failed", "replied", "completed", "converted",
        "won", "lost", "closed", "processing",
        "opt-out", "cancelled",
    }
    terminal_followup = {"completed", "failed"}

    return (
        (status in terminal_statuses)
        or (followup_status in terminal_followup)
        or replied
        or (reply_count > 0)
    )


# ── Tracking URL builder ──────────────────────────────────────────────────────

def _build_tracking_urls(lead_id: int, campaign_id: int) -> Dict[str, str]:
    ts        = int(_now_utc().timestamp())
    pixel_url = f"{PUBLIC_TRACKING_BASE_URL}/open/{lead_id}?campaign_id={campaign_id}&ts={ts}"
    click_url = (
        f"{PUBLIC_TRACKING_BASE_URL}/click/{lead_id}"
        f"?campaign_id={campaign_id}&url={quote(CTA_DESTINATION_URL, safe='')}"
    )
    return {"pixel_url": pixel_url, "cta_url": click_url}


# ── Email content builders ────────────────────────────────────────────────────

def _build_cold_email(lead: Dict[str, Any], campaign_id: int) -> Dict[str, Any]:
    """
    Build cold email content using the personalizer (email_personalizer.py).
    This is ONLY for initial outreach (status='new').
    Uses cold_email / cold_email_saas / cold_email_ecommerce templates.
    """
    lead_id  = int(lead.get("id"))
    tracking = _build_tracking_urls(lead_id, campaign_id)

    # Inject tracking URLs into lead so personalizer can use them
    lead_with_tracking = {
        **lead,
        "cta_url":       tracking["cta_url"],
        "open_tracking_url": tracking["pixel_url"],
        "sender_name":   SENDER_NAME,
    }

    result = personalize_email(lead_with_tracking, step=0, use_dynamic_subject=True)

    subject  = (result.get("subject") or "").strip()
    body     = (result.get("body") or "").strip()
    html_body = (result.get("html_body") or "").strip()

    if not subject or not body:
        return {}

    # Inject pixel into html_body if not already present
    pixel_tag = (
        f'<img src="{tracking["pixel_url"]}" '
        f'width="1" height="1" style="display:none;opacity:0" alt="" />'
    )
    if not html_body:
        html_body = body.replace("\n", "<br>") + pixel_tag
    elif pixel_tag not in html_body and tracking["pixel_url"] not in html_body:
        html_body += pixel_tag

    return {
        "subject":       subject,
        "body":          body,
        "html_body":     html_body,
        "email_type":    "cold",
        "pixel_url":     tracking["pixel_url"],
    }


def _build_followup_email(lead: Dict[str, Any], campaign_id: int, action: str) -> Dict[str, Any]:
    """
    Build follow-up email content using the follow-up state machine templates.
    action is: 'followup_no_open' or 'followup_soft_open'
    """
    lead_id  = int(lead.get("id"))
    tracking = _build_tracking_urls(lead_id, campaign_id)

    content  = get_followup_email_content(action, lead)
    subject  = (content.get("subject") or "").strip()
    body     = (content.get("body") or "").strip()
    html_body = (content.get("html_body") or "").strip()

    if not subject or not body:
        return {}

    # Format template variables
    name      = _lead_name(lead) or "there"
    company   = lead.get("company") or ""
    pain_hook = lead.get("pain_hook") or "low reply rates"

    class _SafeFmt(dict):
        def __missing__(self, key):
            return ""

    ctx = _SafeFmt({
        "name":       name,
        "company":    company,
        "pain_hook":  pain_hook,
        "cta_url":    tracking["cta_url"],
        "sender_name": SENDER_NAME,
    })

    subject   = subject.format_map(ctx).strip()
    body      = body.format_map(ctx).strip()
    html_body = html_body.format_map(ctx).strip() if html_body else body.replace("\n", "<br>")

    pixel_tag = (
        f'<img src="{tracking["pixel_url"]}" '
        f'width="1" height="1" style="display:none;opacity:0" alt="" />'
    )
    if pixel_tag not in html_body and tracking["pixel_url"] not in html_body:
        html_body += pixel_tag

    return {
        "subject":    subject,
        "body":       body,
        "html_body":  html_body,
        "email_type": action,
        "pixel_url":  tracking["pixel_url"],
    }


# ── Actual Gmail send ─────────────────────────────────────────────────────────

def _send_html_email(
    to_email: str,
    subject: str,
    body: str,
    html_body: str,
    thread_id: Optional[str] = None,
) -> Any:
    return send_via_gmail(
        to_email=to_email,
        subject=subject,
        body=body,
        reply_to=REPLY_TO,
        html_body=html_body,
        thread_id=thread_id,
    )


def _extract_retry_after(exc: Exception) -> Optional[datetime]:
    if hasattr(exc, "retry_after_seconds"):
        try:
            secs = int(getattr(exc, "retry_after_seconds"))
            if secs > 0:
                return _now_utc() + timedelta(seconds=secs)
        except Exception:
            pass
    return None


# ── Main sync send ────────────────────────────────────────────────────────────

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

    # Never touch terminal leads
    if _is_terminal(lead):
        return False

    if not _passes_minimum_quality(lead):
        return False

    status = _normalize_text(lead.get("status"))

    # ── PATH 1: Cold outreach (status = 'new') ────────────────────────────────
    if initial_outreach or _is_initial_lead(lead):
        if not _is_initial_lead(lead):
            # initial_outreach=True was passed but lead is not new — skip
            return False

        if not _cooldown_passed(lead):
            return False

        email_content = _build_cold_email(lead, campaign_id)
        if not email_content:
            logger.warning(
                f"Empty cold email payload for {lead_email} — "
                f"check cold_email template in email_templates.json"
            )
            return False

        _mark_processing(lead_id)

        try:
            send_result = _send_html_email(
                to_email=lead_email,
                subject=email_content["subject"],
                body=email_content["body"],
                html_body=email_content["html_body"],
                thread_id=None,  # cold email starts a new thread
            )
            if not send_result:
                raise RuntimeError("send_via_gmail returned no result")

        except GmailRateLimitError as e:
            _mark_rate_limited(lead_id, _extract_retry_after(e))
            store_event(lead_id=lead_id, campaign_id=campaign_id, event_type="rate_limited",
                        metadata={"error": str(e), "channel": "email", "email_type": "cold"})
            logger.error(f"⚠ Rate limited (cold) → {lead_email}: {e}")
            return False

        except Exception as e:
            if "429" in str(e) or "rate limit" in str(e).lower():
                _mark_rate_limited(lead_id, _extract_retry_after(e))
                return False
            _mark_failed_send(lead_id, reason=str(e))
            store_event(lead_id=lead_id, campaign_id=campaign_id, event_type="failed",
                        metadata={"error": str(e), "channel": "email", "email_type": "cold"})
            logger.error(f"❌ Cold send failed → {lead_email}: {e}")
            return False

        thread_id    = send_result.get("thread_id") if isinstance(send_result, dict) else None
        gmail_msg_id = send_result.get("message_id") if isinstance(send_result, dict) else None

        _mark_sent_initial(lead_id, thread_id, gmail_msg_id)

        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="sent",
            metadata={
                "provider":        "gmail",
                "email_type":      "cold",
                "thread_id":       thread_id,
                "gmail_message_id": gmail_msg_id,
                "channel":         "email",
            },
        )

        logger.info(f"✅ Cold email sent → {lead_email}")
        return True

    # ── PATH 2: Follow-up (status = 'sent') ───────────────────────────────────
    if status != "sent":
        return False

    action = decide_followup_action(lead)

    if action is None:
        return False

    # State-only transitions — no email sent
    if action == "__mark_failed__":
        mark_lead_failed(lead_email, campaign_id)
        return False

    if action == "__mark_completed__":
        mark_lead_completed(lead_email, campaign_id)
        return False

    # Sendable follow-up
    email_content = _build_followup_email(lead, campaign_id, action)
    if not email_content:
        logger.warning(f"Empty followup payload for {lead_email} action={action}")
        mark_lead_failed(lead_email, campaign_id)
        return False

    _mark_processing(lead_id)

    try:
        send_result = _send_html_email(
            to_email=lead_email,
            subject=email_content["subject"],
            body=email_content["body"],
            html_body=email_content["html_body"],
            thread_id=lead.get("thread_id") or None,
        )
        if not send_result:
            raise RuntimeError("send_via_gmail returned no result")

    except GmailRateLimitError as e:
        _mark_rate_limited(lead_id, _extract_retry_after(e))
        store_event(lead_id=lead_id, campaign_id=campaign_id, event_type="rate_limited",
                    metadata={"error": str(e), "channel": "email", "email_type": action})
        logger.error(f"⚠ Rate limited (followup) → {lead_email}: {e}")
        return False

    except Exception as e:
        if "429" in str(e) or "rate limit" in str(e).lower():
            _mark_rate_limited(lead_id, _extract_retry_after(e))
            return False
        _mark_failed_send(lead_id, reason=str(e))
        store_event(lead_id=lead_id, campaign_id=campaign_id, event_type="failed",
                    metadata={"error": str(e), "channel": "email", "email_type": action})
        logger.error(f"❌ Follow-up send failed → {lead_email}: {e}")
        return False

    thread_id    = send_result.get("thread_id") if isinstance(send_result, dict) else None
    gmail_msg_id = send_result.get("message_id") if isinstance(send_result, dict) else None

    # Update follow-up state machine
    update_followup_sent(
        lead_email=lead_email,
        campaign_id=campaign_id,
        action=action,
        thread_id=thread_id,
        gmail_message_id=gmail_msg_id,
    )

    store_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="sent",
        metadata={
            "provider":        "gmail",
            "email_type":      action,
            "thread_id":       thread_id,
            "gmail_message_id": gmail_msg_id,
            "channel":         "email",
            "open_count":      _safe_int(lead.get("open_count")),
            "reply_count":     _safe_int(lead.get("reply_count")),
        },
    )

    logger.info(f"✅ Follow-up sent → {lead_email} ({action})")
    return True


# ── Async wrapper ─────────────────────────────────────────────────────────────

async def send_email_async(*args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(send_email_sync, *args, **kwargs))


# ── Bulk send ─────────────────────────────────────────────────────────────────

async def send_bulk_emails(leads: List[dict], concurrency: int = 10, **kwargs) -> List[bool]:
    initial_outreach = bool(kwargs.pop("initial_outreach", False))

    enriched: List[Dict[str, Any]] = []
    seen: set = set()

    for lead in leads:
        email       = lead.get("email")
        campaign_id = lead.get("campaign_id")
        if not email or campaign_id is None:
            continue

        key = (email.strip().lower(), int(campaign_id))
        if key in seen:
            continue

        db = get_lead(email, campaign_id)
        if not db:
            continue

        if _is_terminal(db):
            continue

        if initial_outreach:
            # Only send to leads with status='new' (or pending/not_contacted with no prior send)
            if not _is_initial_lead(db):
                continue
        else:
            # Follow-up path: only status='sent', not terminal, has an action
            if _normalize_text(db.get("status")) != "sent":
                continue
            action = decide_followup_action(db)
            if not action:
                continue

        enriched.append(db)
        seen.add(key)

    if not enriched:
        return []

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def worker(lead: Dict[str, Any]) -> bool:
        async with semaphore:
            low  = min(MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS)
            high = max(MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS)
            if high > 0:
                await asyncio.sleep(random.randint(low, high))
            try:
                return await send_email_async(
                    lead["email"],
                    lead["campaign_id"],
                    initial_outreach=initial_outreach,
                    **kwargs,
                )
            except Exception as e:
                logger.error(f"❌ Unexpected send failure → {lead.get('email')}: {e}")
                return False

    results = await asyncio.gather(*[worker(l) for l in enriched], return_exceptions=False)

    success = sum(1 for r in results if r is True)
    print(f"\n📨 Success: {success}/{len(enriched)}")
    return list(results)
