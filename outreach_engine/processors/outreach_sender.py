# File: outreach_engine/processors/outreach_sender.py

import asyncio
import os
import random
from datetime import datetime, timedelta
from typing import Optional, Any, Dict, List

from outreach_engine.core.retry import retry
from outreach_engine.core.proxy_rotator import get_next_proxy
from outreach_engine.core.email_providers import send_with_fallback
from outreach_engine.core.provider_rotator import (
    get_available_provider,
    increment_provider_usage,
)
from outreach_engine.processors.follow_up_manager import determine_next_step
from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.core.lead_manager import get_lead
from outreach_engine.tracking.event_repository import store_event
from outreach_engine.database.supabase_client import supabase
from outreach_engine.analytics.lead_scoring import calculate_engagement_score
from outreach_engine.core.performance_logger import timer
from outreach_engine.utils.logger import get_logger
from outreach_engine.core.templates import render_template

logger = get_logger(__name__)

TEST_EMAIL = os.getenv("TEST_EMAIL", "").strip().lower()

MIN_SEND_DELAY_SECONDS = int(os.getenv("MIN_SEND_DELAY_SECONDS", "0"))
MAX_SEND_DELAY_SECONDS = int(os.getenv("MAX_SEND_DELAY_SECONDS", "0"))

RESEND_COOLDOWN_HOURS = 12


def _passes_minimum_quality(lead: Dict[str, Any]) -> bool:
    return bool(lead.get("email") and lead.get("company") and lead.get("first_name"))


def _cooldown_passed(lead: Dict[str, Any]) -> bool:
    last = lead.get("last_email_sent")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except:
        return True

    now = datetime.utcnow().replace(tzinfo=last_dt.tzinfo)
    return now - last_dt > timedelta(hours=RESEND_COOLDOWN_HOURS)


def _mark_processing(lead_id: int):
    supabase.table("outreach_leads").update({
        "status": "processing",
        "last_updated": datetime.utcnow().isoformat(),
    }).eq("id", lead_id).execute()


def _mark_sent(lead_id: int, step: int):
    now = datetime.utcnow().isoformat()
    supabase.table("outreach_leads").update({
        "status": "sent",
        "followup_step": step,
        "last_email_sent": now,
        "last_updated": now,
    }).eq("id", lead_id).execute()


def _mark_failed(lead_id: int):
    supabase.table("outreach_leads").update({
        "status": "failed",
        "last_updated": datetime.utcnow().isoformat(),
    }).eq("id", lead_id).execute()


def _is_duplicate_event(lead: dict, event_type: str) -> bool:
    """
    Prevent infinite loops + duplicate open/click/reply updates.
    """
    if event_type == "open":
        return bool(lead.get("email_opened") is True)
    if event_type == "click":
        return False  # allow multiple clicks
    if event_type == "reply":
        return bool(lead.get("reply_status") is not None)
    return False


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

    lead_id = lead["id"]
    status = (lead.get("status") or "").lower()

    # 🛑 ANTI LOOP (FIXED)
    if status in ["processing", "sent", "replied", "converted", "opt-out"]:
        return False

    if not _passes_minimum_quality(lead):
        return False

    if initial_outreach and not _cooldown_passed(lead):
        return False

    provider = get_available_provider()
    if not provider:
        return False

    step = determine_next_step(lead_email, campaign_id)

    base_email = personalize_email(lead, step=step)
    if not base_email:
        return False

    email = render_template("cold_email", {
        "name": lead.get("first_name"),
        "company": lead.get("company"),
        "pain_hook": base_email.get("pain_hook", "low reply rates"),
        "sender_name": "Your Name",
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "link": f"http://localhost:8000/open/{lead_id}?campaign_id={campaign_id}"
    })

    _mark_processing(lead_id)

    try:
        provider_used = send_with_fallback(
            lead_email,
            email["subject"],
            email["body"],
            preferred_provider=provider,
        )

        increment_provider_usage(provider_used)

    except Exception as e:
        _mark_failed(lead_id)

        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="failed",
            metadata={"error": str(e)},
        )
        return False

    _mark_sent(lead_id, step)

    store_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="sent",
        metadata={"provider": provider_used},
    )

    return True


async def send_email_async(*args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, send_email_sync, *args, **kwargs)


async def send_bulk_emails(leads: List[dict], concurrency: int = 10, **kwargs):

    filtered = []
    seen = set()

    for l in leads:
        email = l.get("email")
        if not email:
            continue

        if email in seen:
            continue

        db = get_lead(email, l.get("campaign_id"))
        if not db:
            continue

        if db["status"] in ["sent", "processing", "replied", "converted"]:
            continue

        filtered.append(db)
        seen.add(email)

    sem = asyncio.Semaphore(concurrency)

    async def worker(l):
        async with sem:
            delay = random.randint(MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS)
            if delay:
                await asyncio.sleep(delay)

            return await send_email_async(l["email"], l["campaign_id"])

    return await asyncio.gather(*[worker(x) for x in filtered])