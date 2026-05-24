# outreach_engine/processors/follow_up_scheduler.py
"""
Follow-Up Scheduler — Timing Engine.

Responsibilities:
  - Execute follow-ups at the right time based on delay windows
  - Check lead state BEFORE every send (never send to replied/opt-out)
  - Prevent duplicate sends using scheduler_locks
  - Support configurable send windows (+24h, +72h, +7d)
  - Run as a periodic async loop

Rules:
  - ONLY leads with status == 'sent' are eligible for follow-up
  - replied / converted / opt-out / failed → ALWAYS skip
  - A lead+step combination must never be sent twice (lock mechanism)
  - State is always re-fetched from DB — never trust stale in-memory state

Flow:
  leads list
      ↓
  check_and_send(lead)
      ↓
  re-fetch lead state from DB   ← always fresh
      ↓
  determine_next_step()
      ↓
  acquire scheduler_lock        ← prevents duplicates
      ↓
  check send time window
      ↓
  send_email_async()
      ↓
  update_followup() + log event
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from outreach_engine.processors.outreach_sender import send_email_async
from outreach_engine.processors.follow_up_manager import determine_next_step, update_followup
from outreach_engine.database.supabase_client import supabase


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Delay windows in days per follow-up step (fallback when no AI predictor)
FOLLOWUP_DELAYS_DAYS: Dict[int, int] = {
    0: 1,   # +24h after initial send
    1: 3,   # +72h after step 0
    2: 7,   # +7d  after step 1
    3: 10,  # +10d after step 2
    4: 14,  # +14d after step 3
}

MAX_STEP = max(FOLLOWUP_DELAYS_DAYS.keys())

# Statuses that mean: never send anything to this lead
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

# Only leads in this status are eligible
ELIGIBLE_STATUS = "sent"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string to timezone-aware UTC datetime."""
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
    Compute when the next email should be sent.
    Uses next_followup from DB if set, otherwise falls back to delay table.
    """
    # Prefer the DB-stored next_followup time (set by follow_up_manager)
    db_next = _parse_datetime(lead.get("next_followup"))
    if db_next:
        return db_next

    # Fallback: last_email_sent + delay for this step
    last_sent = _parse_datetime(
        lead.get("last_email_sent") or lead.get("last_email_sent_at")
    )

    if last_sent:
        delay_days = FOLLOWUP_DELAYS_DAYS.get(next_step, 1)
        return last_sent + timedelta(days=delay_days)

    # If we have no reference time, send now
    return _utcnow()


# ---------------------------------------------------------------------------
# Scheduler Lock (prevents duplicate sends)
# ---------------------------------------------------------------------------

def _acquire_lock(lead_key: str) -> bool:
    """
    Try to acquire a scheduler lock for lead_key.
    Returns True if the lock was acquired (safe to send).
    Returns False if the lock already exists (skip this lead).
    """
    try:
        existing = (
            supabase.table("scheduler_locks")
            .select("lead_key")
            .eq("lead_key", lead_key)
            .execute()
        )

        if existing.data:
            return False  # Already locked — skip

        supabase.table("scheduler_locks").insert({
            "lead_key": lead_key,
            "locked_at": _utcnow().isoformat(),
        }).execute()

        return True

    except Exception as e:
        print(f"⚠️ Scheduler lock error for {lead_key}: {e}")
        return False  # Fail safe: don't send if lock is uncertain


def _release_lock(lead_key: str) -> None:
    """Release the scheduler lock after a successful (or failed) send."""
    try:
        supabase.table("scheduler_locks").delete().eq("lead_key", lead_key).execute()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Fresh state fetch
# ---------------------------------------------------------------------------

def _fetch_fresh_lead(lead_id: int, campaign_id: int) -> Optional[Dict]:
    """
    Always re-fetch lead state from DB before any send decision.
    Never rely on the stale lead dict passed from the scheduler loop.
    """
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
# Core: check a single lead and send if appropriate
# ---------------------------------------------------------------------------

async def check_and_send(lead: Dict, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        email = lead.get("email")
        campaign_id = lead.get("campaign_id")
        lead_id = lead.get("id")

        if not email or not campaign_id or not lead_id:
            return

        # --- Step 1: Always re-fetch fresh state from DB ---
        fresh = _fetch_fresh_lead(lead_id, campaign_id)
        if not fresh:
            return

        status = (fresh.get("status") or "").lower().strip()
        followup_step = int(fresh.get("followup_step") or 0)

        # --- Step 2: Hard stop checks ---
        if status in STOP_STATUSES:
            return  # Never send to these leads

        if status != ELIGIBLE_STATUS:
            return  # Only 'sent' leads are eligible for follow-up

        if followup_step >= MAX_STEP:
            return  # Sequence exhausted

        # --- Step 3: Determine next step (rule-based, no clicks) ---
        next_step = determine_next_step(email, campaign_id)
        if next_step == -1:
            return

        if next_step > MAX_STEP:
            return

        # --- Step 4: Check timing window ---
        next_send_time = _compute_next_send_time(fresh, next_step)
        now = _utcnow()

        if now < next_send_time:
            return  # Not yet time to send

        # --- Step 5: Acquire lock to prevent duplicate sends ---
        lead_key = f"{lead_id}:{next_step}"
        if not _acquire_lock(lead_key):
            print(f"🔒 Already locked → {email} step {next_step}")
            return

        # --- Step 6: Final state re-check (race condition guard) ---
        final_check = _fetch_fresh_lead(lead_id, campaign_id)
        if not final_check:
            _release_lock(lead_key)
            return

        final_status = (final_check.get("status") or "").lower().strip()
        if final_status in STOP_STATUSES or final_status != ELIGIBLE_STATUS:
            _release_lock(lead_key)
            return

        # --- Step 7: Send ---
        try:
            sent = await send_email_async(email, campaign_id)

            if sent:
                update_followup(email, campaign_id, step=next_step, status="sent")

                # Log the send event
                try:
                    supabase.table("lead_events").insert({
                        "lead_id": lead_id,
                        "campaign_id": campaign_id,
                        "event_type": "sent",
                        "metadata": {
                            "followup_step": next_step,
                            "channel": "email",
                            "scheduled_at": now.isoformat(),
                        },
                        "created_at": now.isoformat(),
                    }).execute()
                except Exception as e:
                    print(f"⚠️ Failed to log send event for {email}: {e}")

                print(f"✅ Follow-up sent → {email} | step={next_step} | campaign={campaign_id}")
            else:
                # Send failed — release lock so retry is possible
                _release_lock(lead_key)

        except Exception as e:
            print(f"❌ Error sending to {email}: {e}")
            _release_lock(lead_key)


# ---------------------------------------------------------------------------
# Batch scheduler
# ---------------------------------------------------------------------------

async def schedule_followups(leads: List[Dict], concurrency: int = 5) -> None:
    """
    Process a list of leads and send follow-ups where due.

    Args:
        leads: list of lead dicts (may be stale — fresh state is always fetched)
        concurrency: max simultaneous sends
    """
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [check_and_send(lead, semaphore) for lead in leads]
    await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Periodic scheduler loop
# ---------------------------------------------------------------------------

async def run_scheduler_periodically(
    leads: List[Dict],
    interval_minutes: int = 60,
) -> None:
    """
    Run the scheduler on a fixed interval.
    Leads are re-fetched from DB on each cycle via check_and_send → _fetch_fresh_lead.
    """
    while True:
        print(f"🕒 Scheduler running at {_utcnow().isoformat()} UTC")
        await schedule_followups(leads, concurrency=5)
        await asyncio.sleep(interval_minutes * 60)
