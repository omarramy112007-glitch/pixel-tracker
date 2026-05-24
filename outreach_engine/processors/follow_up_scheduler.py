# outreach_engine/processors/follow_up_scheduler.py

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from outreach_engine.core.queue import add_lead_to_queue
from outreach_engine.processors.follow_up_manager import determine_next_step
from outreach_engine.database.supabase_client import supabase


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FOLLOWUP_DELAYS_DAYS: Dict[int, int] = {
    0: 1,   # +24h after initial send
    1: 3,   # +72h after step 0
    2: 7,   # +7d after step 1
    3: 10,  # +10d after step 2
    4: 14,  # +14d after step 3
}

MAX_STEP = max(FOLLOWUP_DELAYS_DAYS.keys())

STOP_STATUSES = {
    "replied",
    "interested",
    "converted",
    "opt-out",
    "unsubscribed",
    "completed",
    "failed",
    "cancelled",
}

ELIGIBLE_STATUS = "sent"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _compute_next_send_time(lead: Dict, next_step: int) -> datetime:
    """
    Prefer DB-stored next_followup. Otherwise:
    last_email_sent + delay for next_step.
    """
    db_next = _parse_datetime(lead.get("next_followup"))
    if db_next:
        return db_next

    last_sent = _parse_datetime(
        lead.get("last_email_sent") or lead.get("last_email_sent_at")
    )
    if last_sent:
        delay_days = FOLLOWUP_DELAYS_DAYS.get(next_step, 1)
        return last_sent + timedelta(days=delay_days)

    return _utcnow()


def _acquire_lock(lead_key: str) -> bool:
    """
    Prevent duplicate enqueueing for the same lead+step.
    """
    try:
        existing = (
            supabase.table("scheduler_locks")
            .select("lead_key")
            .eq("lead_key", lead_key)
            .execute()
        )

        if existing.data:
            return False

        supabase.table("scheduler_locks").insert({
            "lead_key": lead_key,
            "locked_at": _utcnow_iso(),
        }).execute()

        return True

    except Exception as e:
        print(f"⚠️ Scheduler lock error for {lead_key}: {e}")
        return False


def _release_lock(lead_key: str) -> None:
    try:
        supabase.table("scheduler_locks").delete().eq("lead_key", lead_key).execute()
    except Exception:
        pass


def _fetch_fresh_lead(lead_id: int, campaign_id: int) -> Optional[Dict]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .eq("campaign_id", campaign_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    except Exception as e:
        print(f"⚠️ Failed to fetch fresh lead state for id={lead_id}: {e}")
    return None


# ---------------------------------------------------------------------------
# Core: check a single lead and enqueue if appropriate
# ---------------------------------------------------------------------------

async def check_and_enqueue(lead: Dict, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        email = lead.get("email")
        campaign_id = lead.get("campaign_id")
        lead_id = lead.get("id")

        if not email or not campaign_id or not lead_id:
            return

        fresh = _fetch_fresh_lead(lead_id, campaign_id)
        if not fresh:
            return

        status = (fresh.get("status") or "").lower().strip()
        followup_step = int(fresh.get("followup_step") or 0)

        if status in STOP_STATUSES:
            return

        if status != ELIGIBLE_STATUS:
            return

        if followup_step >= MAX_STEP:
            return

        next_step = determine_next_step(email, campaign_id)
        if next_step == -1 or next_step > MAX_STEP:
            return

        next_send_time = _compute_next_send_time(fresh, next_step)
        now = _utcnow()

        if now < next_send_time:
            return

        lead_key = f"{lead_id}:{next_step}"
        if not _acquire_lock(lead_key):
            print(f"🔒 Already locked → {email} step {next_step}")
            return

        try:
            # We ONLY enqueue here — no direct sending.
            result = add_lead_to_queue(
                lead_id=lead_id,
                followup_step=next_step,
                scheduled_at=next_send_time,
                delay_hours=0,
            )

            # Store next follow-up time on the lead for visibility
            try:
                supabase.table("outreach_leads").update({
                    "next_followup": next_send_time.isoformat(),
                    "last_updated": _utcnow_iso(),
                }).eq("id", lead_id).execute()
            except Exception:
                pass

            print(f"🗂️ Enqueued follow-up → {email} | step={next_step} | campaign={campaign_id}")
            return result

        except Exception as e:
            print(f"❌ Failed to enqueue {email}: {e}")
            _release_lock(lead_key)


# ---------------------------------------------------------------------------
# Batch scheduler
# ---------------------------------------------------------------------------

async def schedule_followups(leads: List[Dict], concurrency: int = 5) -> None:
    """
    Process leads and enqueue follow-ups where due.
    No direct sending happens here.
    """
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [check_and_enqueue(lead, semaphore) for lead in leads]
    await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Periodic scheduler loop
# ---------------------------------------------------------------------------

async def run_scheduler_periodically(
    leads: List[Dict],
    interval_minutes: int = 60,
) -> None:
    while True:
        print(f"🕒 Scheduler running at {_utcnow_iso()} UTC")
        await schedule_followups(leads, concurrency=5)
        await asyncio.sleep(interval_minutes * 60)
