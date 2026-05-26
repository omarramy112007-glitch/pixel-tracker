# outreach_engine/processors/follow_up_scheduler.py

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from outreach_engine.database.supabase_client import supabase
from outreach_engine.processors.follow_up_manager import determine_next_step
from outreach_engine.processors.outreach_sender import send_email_async


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_TO_SCAN = 500
STOP_STATUSES = {
    "replied",
    "interested_followup",
    "converted",
    "opt-out",
    "opt_out",
    "unsubscribed",
    "completed",
    "failed",
    "cancelled",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    try:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _fetch_due_leads(limit: int = MAX_TO_SCAN) -> List[Dict]:
    """
    Only leads with status='sent' and due next_followup are eligible.
    """
    now = _utcnow_iso()

    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("status", "sent")
            .lte("next_followup", now)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"⚠️ _fetch_due_leads failed: {e}")
        return []


def _fresh_state(lead_id: int, campaign_id: int) -> Optional[Dict]:
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
        print(f"⚠️ _fresh_state failed for lead_id={lead_id}: {e}")
    return None


# ---------------------------------------------------------------------------
# Core worker
# ---------------------------------------------------------------------------

async def _check_and_send(lead: Dict, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        email = lead.get("email")
        campaign_id = lead.get("campaign_id")
        lead_id = lead.get("id")

        if not email or not campaign_id or not lead_id:
            return

        fresh = _fresh_state(lead_id, campaign_id)
        if not fresh:
            return

        status = (fresh.get("status") or "").strip().lower()
        if status in STOP_STATUSES:
            return

        next_step = determine_next_step(email, campaign_id)
        if next_step == -1:
            return

        try:
            sent = await send_email_async(email, campaign_id)
            if not sent:
                return

            print(f"✅ Follow-up sent → {email} | campaign={campaign_id}")

        except Exception as e:
            print(f"❌ Follow-up send failed → {email}: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def schedule_followups(leads: Optional[List[Dict]] = None, concurrency: int = 5) -> None:
    """
    Send follow-ups for leads that are still in status='sent' and are due.

    The sender will decide whether the follow-up is:
      - followup_no_open
      - followup_soft_open
      - interested_followup

    based on open_count / reply_count.
    """
    due = leads if leads is not None else _fetch_due_leads()

    if not due:
        return

    semaphore = asyncio.Semaphore(concurrency)
    tasks = [_check_and_send(lead, semaphore) for lead in due]
    await asyncio.gather(*tasks, return_exceptions=True)


async def run_scheduler_periodically(
    leads: Optional[List[Dict]] = None,
    interval_minutes: int = 60,
) -> None:
    while True:
        print(f"🕒 Scheduler running at {_utcnow_iso()} UTC")
        await schedule_followups(leads=leads, concurrency=5)
        await asyncio.sleep(interval_minutes * 60)
