# outreach_engine/schedulers/weekly_reset.py

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from apscheduler.schedulers.background import BackgroundScheduler

from outreach_engine.database.supabase_client import supabase

RETENTION_DAYS = 7
BATCH_SIZE = 1000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _chunk(items: List[int], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _fetch_stale_lead_ids(cutoff: datetime) -> List[int]:
    """
    Returns lead IDs older than the cutoff date.
    We use outreach_leads.created_at as the source of truth for weekly rollover.
    """
    lead_ids: List[int] = []
    offset = 0

    while True:
        try:
            resp = (
                supabase.table("outreach_leads")
                .select("id, created_at")
                .lt("created_at", cutoff.isoformat())
                .range(offset, offset + BATCH_SIZE - 1)
                .execute()
            )
        except Exception as e:
            print(f"❌ Failed to fetch stale lead IDs: {e}")
            break

        rows = resp.data or []
        if not rows:
            break

        lead_ids.extend([row["id"] for row in rows if row.get("id") is not None])

        if len(rows) < BATCH_SIZE:
            break

        offset += BATCH_SIZE

    return lead_ids


def _reset_outreach_leads(lead_ids: List[int]) -> int:
    """
    Resets per-lead outreach counters in outreach_leads.
    """
    if not lead_ids:
        return 0

    updated = 0
    now = _utc_now().isoformat()

    for ids in _chunk(lead_ids, 100):
        try:
            supabase.table("outreach_leads").update({
                "open_count": 0,
                "click_count": 0,
                "reply_count": 0,
                "conversion_count": 0,
                "last_updated": now,
            }).in_("id", ids).execute()
            updated += len(ids)
        except Exception as e:
            print(f"⚠ Failed to reset outreach_leads batch: {e}")

    return updated


def _reset_crm_analytics(lead_ids: List[int]) -> int:
    """
    Resets per-lead CRM analytics counters for the same stale leads.
    """
    if not lead_ids:
        return 0

    updated = 0

    for ids in _chunk(lead_ids, 100):
        try:
            supabase.table("crm_analytics").update({
                "emails_sent": 0,
                "opens": 0,
                "clicks": 0,
                "replies": 0,
                "conversions": 0,
                "last_activity": None,
                "engagement_score": 0,
            }).in_("lead_id", ids).execute()
            updated += len(ids)
        except Exception as e:
            print(f"⚠ Failed to reset crm_analytics batch: {e}")

    return updated


def weekly_reset() -> dict:
    """
    Weekly rollover job.

    Safe behavior:
    - Resets outreach counters for leads older than RETENTION_DAYS
    - Resets matching CRM analytics rows
    - Does NOT delete lead_events
    - Does NOT change lead status

    If you prefer lifetime totals with weekly dashboard views,
    keep the dashboard filter-based approach too.
    """
    cutoff = _utc_now() - timedelta(days=RETENTION_DAYS)

    print("\n🧹 Starting weekly reset...")
    print(f"📅 Cutoff: leads created before {cutoff.isoformat()}")

    stale_ids = _fetch_stale_lead_ids(cutoff)

    if not stale_ids:
        print("✅ No stale leads found. Nothing to reset.")
        return {
            "status": "ok",
            "reset_leads": 0,
            "reset_crm_rows": 0,
        }

    reset_leads = _reset_outreach_leads(stale_ids)
    reset_crm_rows = _reset_crm_analytics(stale_ids)

    print(f"✅ Weekly reset complete")
    print(f"   • outreach_leads reset: {reset_leads}")
    print(f"   • crm_analytics reset: {reset_crm_rows}\n")

    return {
        "status": "ok",
        "reset_leads": reset_leads,
        "reset_crm_rows": reset_crm_rows,
        "cutoff": cutoff.isoformat(),
    }


def start_weekly_reset_scheduler(run_immediately: bool = False) -> BackgroundScheduler:
    """
    Starts a background scheduler that runs once per week.
    Default schedule: Sunday 00:05 UTC.
    """
    scheduler = BackgroundScheduler(timezone="UTC")

    scheduler.add_job(
        weekly_reset,
        trigger="cron",
        day_of_week="sun",
        hour=0,
        minute=5,
        id="weekly_reset_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    if run_immediately:
        scheduler.add_job(
            weekly_reset,
            trigger="date",
            run_date=_utc_now() + timedelta(seconds=5),
            id="weekly_reset_immediate",
            replace_existing=True,
        )

    scheduler.start()
    print("🚀 Weekly reset scheduler started (runs every Sunday at 00:05 UTC)")
    return scheduler


if __name__ == "__main__":
    # Manual run mode:
    # python -m outreach_engine.schedulers.weekly_reset
    weekly_reset()