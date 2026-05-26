# outreach_engine/processors/outreach_sender.py

from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from outreach_engine.core.retry import retry
from outreach_engine.core.lead_manager import get_lead
from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase
from outreach_engine.core.performance_logger import timer
from outreach_engine.utils.logger import get_logger
from outreach_engine.core.gmail_sender import send_via_gmail
from outreach_engine.processors.follow_up_manager import (
    determine_next_step,
    generate_next_email,
    update_followup,
)

logger = get_logger(__name__)

TEST_EMAIL = os.getenv("TEST_EMAIL", "").strip().lower()
MIN_SEND_DELAY_SECONDS = int(os.getenv("MIN_SEND_DELAY_SECONDS", "0"))
MAX_SEND_DELAY_SECONDS = int(os.getenv("MAX_SEND_DELAY_SECONDS", "0"))
RESEND_COOLDOWN_HOURS = 12
SENDER_NAME = os.getenv("SENDER_NAME", "Your Name").strip()
REPLY_TO = os.getenv("REPLY_TO", "").strip() or None
PUBLIC_TRACKING_BASE_URL = (
    os.getenv("PUBLIC_TRACKING_BASE_URL")
    or os.getenv("TRACKING_BASE_URL")
    or os.getenv("NGROK_URL")
    or "https://YOUR_PUBLIC_DOMAIN"
).rstrip("/")
CTA_DESTINATION_URL = os.getenv("CTA_DESTINATION_URL", "https://your-landing-page.com").strip()


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def _normalize_text(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _lead_name(lead: Dict[str, Any]) -> str:
    first = lead.get("first_name") or ""
    last = lead.get("last_name") or ""
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
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _cooldown_passed(lead: Dict[str, Any]) -> bool:
    last = _parse_dt(lead.get("last_email_sent"))
    if not last:
        return True
    now = datetime.now(last.tzinfo) if last.tzinfo else datetime.utcnow()
    return now - last > timedelta(hours=RESEND_COOLDOWN_HOURS)


def _set_lead_fields(email: str, campaign_id: int, data: Dict[str, Any]) -> None:
    try:
        supabase.table("outreach_leads").update(data).eq("email", email).eq("campaign_id", campaign_id).execute()
    except Exception as e:
        logger.warning(f"Lead update failed for {email}: {e}")


def _mark_processing(lead_email: str, campaign_id: int, step: int) -> None:
    now = datetime.utcnow().isoformat()
    _set_lead_fields(
        lead_email,
        campaign_id,
        {
            "status": "processing",
            "followup_step": step,
            "last_updated": now,
        },
    )


def _mark_failed(lead_email: str, campaign_id: int) -> None:
    _set_lead_fields(
        lead_email,
        campaign_id,
        {
            "status": "failed",
            "last_updated": datetime.utcnow().isoformat(),
        },
    )


def _mark_sent(lead_email: str, campaign_id: int, step: int) -> None:
    now = datetime.utcnow().isoformat()
    _set_lead_fields(
        lead_email,
        campaign_id,
        {
            "status": "sent",
            "followup_step": step,
            "last_email_sent": now,
            "last_contacted": now,
            "last_updated": now,
        },
    )


def _should_send_initial(lead: Dict[str, Any]) -> bool:
    return _normalize_text(lead.get("status")) in {"new", "pending", "not_contacted", ""}


def _should_send_followup(lead: Dict[str, Any], next_step: int) -> bool:
    """
    Only one follow-up is sent automatically after the initial send.
    """
    status = _normalize_text(lead.get("status"))
    current_step = int(lead.get("followup_step") or 0)

    if status != "sent":
        return False
    if next_step == -1:
        return False
    if next_step <= current_step:
        return False

    return True


def _safe_format(text: Optional[str], context: Dict[str, Any]) -> str:
    if not text:
        return ""

    return str(text).format_map(_SafeDict(context))


def _build_tracking_urls(lead_id: int, campaign_id: int) -> Dict[str, str]:
    ts = int(datetime.utcnow().timestamp())

    click_url = (
        f"{PUBLIC_TRACKING_BASE_URL}/click/{lead_id}"
        f"?campaign_id={campaign_id}"
        f"&url={quote(CTA_DESTINATION_URL, safe='')}"
    )
    pixel_url = f"{PUBLIC_TRACKING_BASE_URL}/open/{lead_id}?campaign_id={campaign_id}&ts={ts}"

    return {
        "cta_url": click_url,
        "pixel_url": pixel_url,
    }


def _build_email_payload(lead: Dict[str, Any], campaign_id: int) -> Dict[str, Any]:
    lead_email = lead.get("email")
    lead_id = lead.get("id")

    email_meta = generate_next_email(lead_email, campaign_id)
    if not email_meta or not email_meta.get("subject") or not email_meta.get("body"):
        return {}

    tracking = _build_tracking_urls(lead_id, campaign_id)

    context = {
        **lead,
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "sender_name": SENDER_NAME,
        "cta_text": "Click here to learn more.",
        "cta_url": tracking["cta_url"],
        "resource_link": tracking["cta_url"],
        "pixel_tag": f'<img src="{tracking["pixel_url"]}" width="1" height="1" style="display:none;opacity:0" alt="" />',
        "dynamic_offer": lead.get("dynamic_offer") or "our automated outreach system",
        "pain_hook": lead.get("pain_hook") or "low reply rates",
        "name": _lead_name(lead) or "there",
        "company": lead.get("company") or "",
    }

    subject = _safe_format(email_meta.get("subject"), context)
    body = _safe_format(email_meta.get("body"), context)
    html_body = _safe_format(email_meta.get("html_body") or "", context)

    # If no HTML template exists, create a simple HTML version so the open pixel works.
    if not html_body:
        html_body = body.replace("\n", "<br>") + context["pixel_tag"]
    else:
        if context["pixel_tag"] not in html_body:
            html_body += context["pixel_tag"]

    # Safety cleanup: never allow localhost / 127.0.0.1 in outbound copy.
    for bad in ("http://localhost", "https://localhost", "http://127.0.0.1", "https://127.0.0.1"):
        subject = subject.replace(bad, "")
        body = body.replace(bad, "")
        html_body = html_body.replace(bad, "")

    return {
        "subject": subject,
        "body": body,
        "html_body": html_body,
        "step": int(email_meta.get("step") or 0),
        "followup_type": email_meta.get("followup_type") or "sent",
    }


def _send_html_email(to_email: str, subject: str, body: str, html_body: str) -> bool:
    return send_via_gmail(
        to_email=to_email,
        subject=subject,
        body=body,
        reply_to=REPLY_TO,
        html_body=html_body,
    )


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

    lead_id = lead.get("id")
    status = _normalize_text(lead.get("status"))

    if status in {"processing", "replied", "converted", "opt-out", "failed"}:
        return False

    if not _passes_minimum_quality(lead):
        return False

    # Decide whether this is an initial send or the single follow-up.
    if _should_send_initial(lead) or initial_outreach:
        step = 0
        followup_type = "sent"
        can_send = _should_send_initial(lead) or initial_outreach
    else:
        next_step = determine_next_step(lead_email, campaign_id)
        step = next_step
        can_send = _should_send_followup(lead, next_step)

        if can_send:
            followup_type = _build_email_payload(lead, campaign_id).get("followup_type", "followup_no_open")
        else:
            followup_type = ""

    if not can_send:
        return False

    email = _build_email_payload(lead, campaign_id)
    if not email or not email.get("subject") or not email.get("body") or not email.get("html_body"):
        return False

    _mark_processing(lead_email, campaign_id, step)

    try:
        _send_html_email(
            to_email=lead_email,
            subject=email["subject"],
            body=email["body"],
            html_body=email["html_body"],
        )
    except Exception as e:
        _mark_failed(lead_email, campaign_id)

        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="failed",
            metadata={"error": str(e)},
        )
        logger.error(f"❌ Failed → {lead_email}: {e}")
        return False

    # Mark the raw send first, then finalize the state after success.
    _mark_sent(lead_email, campaign_id, step)

    store_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="sent",
        metadata={
            "provider": "gmail",
            "step": step,
            "followup_type": email.get("followup_type", "sent"),
        },
    )

    # Final status after a successful send:
    #   - initial send -> sent
    #   - follow-up send -> followup_no_open / followup_soft_open / interested_followup
    update_followup(
        lead_email=lead_email,
        campaign_id=campaign_id,
        step=step,
        status=email.get("followup_type", "sent"),
    )

    logger.info(
        f"✅ Sent → {lead_email} "
        f"(step {step}, type={email.get('followup_type', 'sent')})"
    )
    return True


async def send_email_async(*args, **kwargs):
    loop = asyncio.get_running_loop()
    fn = partial(send_email_sync, *args, **kwargs)
    return await loop.run_in_executor(None, fn)


async def send_bulk_emails(leads: List[dict], concurrency: int = 10, **kwargs):
    """
    Bulk send helper:
      - sends initial emails for new/pending leads
      - sends the one due follow-up for leads still in status='sent'
      - skips terminal/follow-up-final states
    """
    enriched = []
    seen = set()

    for lead in leads:
        email = lead.get("email")
        campaign_id = lead.get("campaign_id")
        if not email or campaign_id is None:
            continue

        key = (email.strip().lower(), int(campaign_id))
        if key in seen:
            continue

        db = get_lead(email, campaign_id)
        if not db:
            continue

        db_status = _normalize_text(db.get("status"))

        if db_status in {"processing", "replied", "converted", "failed", "opt-out"}:
            continue

        if db_status in {"followup_no_open", "followup_soft_open", "interested_followup"}:
            continue

        # Include initial lead, or a sent lead whose follow-up is due.
        if _should_send_initial(db) or (db_status == "sent" and determine_next_step(email, campaign_id) != -1):
            enriched.append(db)
            seen.add(key)

    semaphore = asyncio.Semaphore(concurrency)

    async def send_limited(lead):
        async with semaphore:
            low = min(MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS)
            high = max(MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS)
            delay = random.randint(low, high) if high > 0 else 0
            if delay:
                await asyncio.sleep(delay)

            return await send_email_async(
                lead["email"],
                lead["campaign_id"],
                **kwargs,
            )

    tasks = [send_limited(l) for l in enriched]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success = sum(1 for r in results if r is True)
    print(f"\n📨 Success: {success}/{len(enriched)}")

    return results
