# outreach_engine/processors/follow_up_scheduler.py

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from outreach_engine.database.supabase_client import supabase
from outreach_engine.core.lead_manager import mark_followup_variant_by_id
from outreach_engine.processors.outreach_sender import send_email_async


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_TO_SCAN = 500

ACTIVE_FOLLOWUP_STATUSES = {
    "sent",
    "followup_no_open",
    "followup_soft_open",
    "interested_followup",
}

STOP_STATUSES = {
    "replied",
    "converted",
    "opt-out",
    "opt_out",
    "unsubscribed",
    "completed",
    "failed",
    "cancelled",
}

FOLLOWUP_DELAY_HOURS = {
    "followup_no_open":   48,
    "followup_soft_open": 24,
    "interested_followup": 12,
}

# Maps the variant we are about to send to the followup_status value
# that must NOT already be set, preventing double-sends of the same type.
_VARIANT_TO_FOLLOWUP_STATUS = {
    "followup_no_open":   "no_open",
    "followup_soft_open": "soft_open",
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


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower().replace("_", "-")


def _fetch_due_leads(limit: int = MAX_TO_SCAN) -> List[Dict]:
    """
    Pull only active follow-up leads from the DB, then filter by due
    time in Python. This avoids missing rows where next_followup is NULL.
    """
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .in_("status", list(ACTIVE_FOLLOWUP_STATUSES))
            .limit(limit)
            .execute()
        )
        rows = res.data or []
    except Exception as e:
        print(f"⚠️ _fetch_due_leads failed: {e}")
        return []

    now = datetime.now(timezone.utc)
    due: List[Dict] = []

    for row in rows:
        status = _norm(row.get("status"))
        if status in STOP_STATUSES:
            continue
        next_followup = _parse_dt(row.get("next_followup"))
        if next_followup is None or next_followup <= now:
            due.append(row)

    return due


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


def _followup_variant_for(lead: Dict) -> str:
    """
    Decide which follow-up type to send based on current engagement.

    FIX: checks BOTH open_count AND followup_open_count.

    Previously only open_count was checked. A lead that opened the
    follow-up email has followup_open_count > 0 but open_count == 0,
    so the old code sent followup_no_open again instead of
    followup_soft_open.

    Clicks are ignored by design (clicks alone do not signal enough
    intent to change the follow-up path).
    """
    reply_count         = int(lead.get("reply_count")         or 0)
    open_count          = int(lead.get("open_count")          or 0)
    followup_open_count = int(lead.get("followup_open_count") or 0)

    # Any open — whether on the cold email or the follow-up — means
    # the prospect saw the message. Send the softer persuasion email.
    any_open = (open_count > 0) or (followup_open_count > 0)

    if reply_count > 0:
        return "interested_followup"
    if any_open:
        return "followup_soft_open"
    return "followup_no_open"


def _followup_delay_hours(variant: str) -> int:
    return FOLLOWUP_DELAY_HOURS.get(_norm(variant), 24)


def _is_active_followup_state(status: str) -> bool:
    return _norm(status) in ACTIVE_FOLLOWUP_STATUSES


def _already_sent_this_variant(fresh: Dict, variant: str) -> bool:
    """
    Idempotency guard.

    Prevents sending the same follow-up variant twice if the scheduler
    runs again before the DB has been fully updated, or if a lead is
    accidentally processed by two concurrent workers.

    Maps variant → expected followup_status value and checks the DB.
    """
    expected_status = _VARIANT_TO_FOLLOWUP_STATUS.get(variant)
    if not expected_status:
        return False

    current_followup_status = _norm(fresh.get("followup_status") or "")

    # Normalize stored value: "no_open" == "no-open" etc.
    normalized_current = current_followup_status.replace("-", "_")
    normalized_expected = expected_status.replace("-", "_")

    if normalized_current == normalized_expected:
        print(
            f"⏭️ Skipping {variant} — already sent "
            f"(followup_status={current_followup_status})"
        )
        return True
    return False


def _write_sent_email_type_to_db(lead_id: int, campaign_id: int) -> None:
    """
    FIX: Write sent_email_type='followup' to DB BEFORE calling
    send_email_async() so that if the pixel fires immediately after
    send, pixel_server._resolve_email_type() reads the correct value
    from the DB (Priority 2 fallback) and routes the open to
    followup_open_count instead of open_count.

    This eliminates the race condition where the pixel fires between
    send_email_async() and mark_followup_variant_by_id().
    """
    try:
        supabase.table("outreach_leads").update({
            "sent_email_type": "followup",
        }).eq("id", lead_id).eq("campaign_id", campaign_id).execute()
        print(
            f"📝 Pre-send: sent_email_type=followup written "
            f"→ lead_id={lead_id}"
        )
    except Exception as e:
        print(f"⚠️ Pre-send sent_email_type write failed → lead_id={lead_id}: {e}")


# ---------------------------------------------------------------------------
# Core worker
# ---------------------------------------------------------------------------

async def _check_and_send(lead: Dict, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        email       = lead.get("email")
        campaign_id = lead.get("campaign_id")
        lead_id     = lead.get("id")

        if not email or not campaign_id or not lead_id:
            return

        # Always re-fetch fresh state immediately before processing
        # so we work from the latest DB values, not a stale poll snapshot.
        fresh = _fresh_state(lead_id, campaign_id)
        if not fresh:
            return

        status = _norm(fresh.get("status"))
        if status in STOP_STATUSES:
            return

        if not _is_active_followup_state(status):
            return

        next_followup = _parse_dt(fresh.get("next_followup"))
        if next_followup is not None and next_followup > datetime.now(timezone.utc):
            return

        # Determine which variant to send using fresh data with the
        # fixed helper that checks both open_count AND followup_open_count.
        followup_variant = _followup_variant_for(fresh)

        # FIX: idempotency guard — skip if this variant was already sent.
        if _already_sent_this_variant(fresh, followup_variant):
            return

        try:
            # FIX: Write sent_email_type='followup' to DB BEFORE sending
            # so pixel_server can read it immediately when the pixel fires.
            # This eliminates the race condition between send and DB update.
            _write_sent_email_type_to_db(lead_id, campaign_id)

            # FIX: Pass email_type="followup" to send_email_async so it
            # embeds ?email_type=followup in the pixel URL (Priority 1
            # in pixel_server._resolve_email_type). This is the most
            # reliable routing signal — it is baked into the email itself.
            sent = await send_email_async(
                email,
                campaign_id,
                email_type="followup",
            )
            if not sent:
                # Revert sent_email_type if send failed so the DB stays clean.
                try:
                    supabase.table("outreach_leads").update({
                        "sent_email_type": None,
                    }).eq("id", lead_id).eq("campaign_id", campaign_id).execute()
                except Exception:
                    pass
                return

            # Write the full follow-up state after confirmed send.
            mark_followup_variant_by_id(
                lead_id=lead_id,
                campaign_id=campaign_id,
                variant=followup_variant,
                delay_hours=_followup_delay_hours(followup_variant),
            )

            print(
                f"✅ Follow-up sent → {email} | "
                f"{followup_variant} | campaign={campaign_id}"
            )

        except Exception as e:
            print(f"❌ Follow-up send failed → {email}: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def schedule_followups(
    leads: Optional[List[Dict]] = None,
    concurrency: int = 5,
) -> None:
    """
    Send follow-ups only for active follow-up states.

    Routing (uses both open_count AND followup_open_count):
      open_count == 0 AND followup_open_count == 0 AND reply_count == 0
          → followup_no_open
      (open_count > 0 OR followup_open_count > 0) AND reply_count == 0
          → followup_soft_open
      reply_count > 0
          → interested_followup

    Failed / converted / opt-out / replied are never touched.
    """
    source = leads if leads is not None else _fetch_due_leads()

    if not source and leads is not None:
        # Fallback to live DB if a stale/incorrect lead list was passed in.
        source = _fetch_due_leads()

    if not source:
        return

    due  = []
    now  = datetime.now(timezone.utc)

    for lead in source:
        status = _norm(lead.get("status"))
        if status in STOP_STATUSES:
            continue
        if not _is_active_followup_state(status):
            continue
        next_followup = _parse_dt(lead.get("next_followup"))
        if next_followup is None or next_followup <= now:
            due.append(lead)

    if not due:
        return

    semaphore = asyncio.Semaphore(concurrency)
    tasks     = [_check_and_send(lead, semaphore) for lead in due]
    await asyncio.gather(*tasks, return_exceptions=True)


async def run_scheduler_periodically(
    leads: Optional[List[Dict]] = None,
    interval_minutes: int = 60,
    use_ai: bool = True,  # kept for backward compatibility
) -> None:
    """
    Background loop for follow-up sending.
    `use_ai` is kept only so older callers do not break.
    """
    while True:
        print(f"🕒 Scheduler running at {_utcnow_iso()} UTC")
        await schedule_followups(leads=leads, concurrency=5)
        await asyncio.sleep(interval_minutes * 60)
