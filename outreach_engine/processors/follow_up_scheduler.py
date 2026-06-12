# outreach_engine/processors/follow_up_scheduler.py

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from outreach_engine.database.supabase_client import supabase
from outreach_engine.processors.outreach_sender import send_email_async

STOP_STATUSES = {
    "replied", "converted", "failed", "completed",
    "won", "lost", "closed", "opt-out", "cancelled",
}

BATCH_SIZE = 1000  # fetch N leads per scheduler run


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _fetch_leads() -> List[Dict]:
    """
    Fetch leads that need processing.
    - Cold: status in new/pending/not_contacted, no last_email_sent
    - Followup: status=sent, next_followup is due
    Filters in Python to support very large tables.
    """
    try:
        res = (
            supabase.table("outreach_leads")
            .select(
                "id, email, campaign_id, status, followup_status, "
                "next_followup, last_email_sent, open_count, "
                "followup_open_count, reply_count"
            )
            .not_.in_("status", list(STOP_STATUSES))
            .limit(BATCH_SIZE)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"⚠ _fetch_leads failed: {e}")
        return []


def _needs_processing(lead: Dict) -> bool:
    status = _norm(lead.get("status"))

    if status in STOP_STATUSES:
        return False

    # Cold outreach — never emailed
    if status in {"new", "pending", "not_contacted", ""} and not lead.get("last_email_sent"):
        return True

    # Followup — status=sent and next_followup is due
    if status == "sent":
        nxt = _parse_dt(lead.get("next_followup"))
        if nxt is None or nxt <= _now():
            return True

    return False


async def _process(lead: Dict, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        email       = lead.get("email")
        campaign_id = lead.get("campaign_id")
        if not email or not campaign_id:
            return
        try:
            status = _norm(lead.get("status"))
            is_cold = status in {"new", "pending", "not_contacted", ""} \
                      and not lead.get("last_email_sent")
            await send_email_async(
                email,
                campaign_id,
                initial_outreach=is_cold,
            )
        except Exception as e:
            print(f"❌ Scheduler process failed → {email}: {e}")


async def schedule_followups(
    leads: Optional[List[Dict]] = None,
    concurrency: int = 20,
) -> None:
    source = leads if leads is not None else _fetch_leads()
    due    = [l for l in source if _needs_processing(l)]

    if not due:
        return

    print(f"🕒 Scheduler: {len(due)} leads to process")
    sem   = asyncio.Semaphore(concurrency)
    tasks = [_process(lead, sem) for lead in due]
    await asyncio.gather(*tasks, return_exceptions=True)


async def run_scheduler_periodically(
    leads: Optional[List[Dict]] = None,
    interval_minutes: int = 60,
    **kwargs,
) -> None:
    while True:
        print(f"🕒 Scheduler tick → {datetime.now(timezone.utc).isoformat()}")
        await schedule_followups(leads=leads)
        await asyncio.sleep(interval_minutes * 60)
