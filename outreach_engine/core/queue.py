# outreach_engine/core/queue.py

# outreach_engine/core/queue.py

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from outreach_engine.database.supabase_client import supabase

MAX_RETRY_COUNT = 5
MAX_RESCHEDULE_SECONDS = 24 * 60 * 60  # 24 hours


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _queue_exists(
    lead_id: Any,
    followup_step: int,
    statuses: Optional[List[str]] = None,
) -> bool:
    try:
        query = (
            supabase.table("outreach_queue")
            .select("id, status, followup_step, lead_id")
            .eq("lead_id", lead_id)
            .eq("followup_step", followup_step)
        )

        res = query.execute()
        rows = res.data or []
        if not rows:
            return False

        if statuses:
            return any((row.get("status") or "").lower() in statuses for row in rows)

        return True
    except Exception:
        return False


def enqueue_followup(
    lead_id: Any,
    followup_step: int = 0,
    delay_hours: int = 0,
    scheduled_at: Optional[Any] = None,
    reason: Optional[str] = None,
):
    """
    Insert a lead into the outreach queue.
    Duplicate protection is based on (lead_id, followup_step).
    """
    if _queue_exists(lead_id, followup_step, statuses=["pending", "processing"]):
        return {"status": "duplicate", "lead_id": lead_id, "followup_step": followup_step}

    when = _parse_iso(scheduled_at) if scheduled_at else None
    if when is None:
        when = _now_utc() + timedelta(hours=delay_hours)

    payload: Dict[str, Any] = {
        "lead_id": lead_id,
        "followup_step": followup_step,
        "status": "pending",
        "scheduled_at": when.isoformat(),
        "retry_count": 0,
        "created_at": _now_iso(),
        "last_attempt": None,
        "last_error": None,
    }

    # Keep payload minimal unless your table has extra columns.
    # `reason` is intentionally not persisted unless your schema supports it.
    return supabase.table("outreach_queue").insert(payload).execute()


def add_lead_to_queue(
    lead_id: Any,
    followup_step: int = 0,
    delay_hours: int = 0,
    scheduled_at: Optional[Any] = None,
    reason: Optional[str] = None,
):
    return enqueue_followup(
        lead_id=lead_id,
        followup_step=followup_step,
        delay_hours=delay_hours,
        scheduled_at=scheduled_at,
        reason=reason,
    )


def fetch_next_batch(limit: int = 20):
    """
    Get the next batch of queue items ready to process.
    """
    try:
        response = (
            supabase.table("outreach_queue")
            .select("*")
            .eq("status", "pending")
            .lte("scheduled_at", _now_iso())
            .order("scheduled_at", ascending=True)
            .limit(limit)
            .execute()
        )
        return response.data or []
    except Exception as e:
        print(f"⚠️ fetch_next_batch error: {e}")
        return []


def mark_processing(queue_id: Any):
    try:
        return (
            supabase.table("outreach_queue")
            .update(
                {
                    "status": "processing",
                    "last_attempt": _now_iso(),
                    "last_error": None,
                }
            )
            .eq("id", queue_id)
            .execute()
        )
    except Exception as e:
        print(f"⚠️ mark_processing error: {e}")
        return None


def mark_sent(queue_id: Any):
    """
    Mark a queued item as sent.
    """
    try:
        return (
            supabase.table("outreach_queue")
            .update(
                {
                    "status": "sent",
                    "last_attempt": _now_iso(),
                    "last_error": None,
                }
            )
            .eq("id", queue_id)
            .execute()
        )
    except Exception as e:
        print(f"⚠️ mark_sent error: {e}")
        return None


def mark_cancelled(queue_id: Any, reason: str = "stopped_state"):
    """
    Cancel an item that should no longer be sent.
    """
    try:
        return (
            supabase.table("outreach_queue")
            .update(
                {
                    "status": "cancelled",
                    "last_attempt": _now_iso(),
                    "last_error": reason,
                }
            )
            .eq("id", queue_id)
            .execute()
        )
    except Exception as e:
        print(f"⚠️ mark_cancelled error: {e}")
        return None


def mark_failed(queue_id: Any, retry_after_seconds: Optional[int] = None):
    """
    Mark a queued item as failed and reschedule it if retries remain.
    """
    try:
        current_res = (
            supabase.table("outreach_queue")
            .select("retry_count")
            .eq("id", queue_id)
            .limit(1)
            .execute()
        )
        current_rows = current_res.data or []

        retry_count = 0
        if current_rows:
            retry_count = int(current_rows[0].get("retry_count") or 0)

        next_retry_count = retry_count + 1

        if next_retry_count >= MAX_RETRY_COUNT:
            payload = {
                "status": "failed",
                "last_attempt": _now_iso(),
                "retry_count": next_retry_count,
                "last_error": "max_retries_reached",
            }
        else:
            if retry_after_seconds is not None and retry_after_seconds > 0:
                delay_seconds = int(retry_after_seconds)
            else:
                delay_seconds = min(
                    60 * (2 ** max(0, next_retry_count - 1)),
                    MAX_RESCHEDULE_SECONDS,
                )

            delay_seconds = min(delay_seconds, MAX_RESCHEDULE_SECONDS)

            payload = {
                "status": "pending",
                "scheduled_at": (_now_utc() + timedelta(seconds=delay_seconds)).isoformat(),
                "last_attempt": _now_iso(),
                "retry_count": next_retry_count,
                "last_error": "retry_scheduled",
            }

        return (
            supabase.table("outreach_queue")
            .update(payload)
            .eq("id", queue_id)
            .execute()
        )

    except Exception as e:
        print(f"⚠️ mark_failed error: {e}")
        return None


def mark_rate_limited(queue_id: Any, retry_after_seconds: Optional[int] = None):
    """
    Alias for mark_failed used when Gmail returns 429.
    """
    return mark_failed(queue_id, retry_after_seconds=retry_after_seconds)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def _fetch_lead_for_queue_item(lead_id: Any) -> Optional[Dict[str, Any]]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    except Exception as e:
        print(f"⚠️ outreach_leads lookup failed: {e}")

    return None


async def process_queue_item(item: Dict[str, Any]) -> bool:
    """
    Process one queue item by sending the email through the outreach sender.
    The actual step selection still happens inside outreach_sender / follow_up_manager.
    """
    queue_id = item.get("id")
    lead_id = item.get("lead_id")

    if queue_id is None or lead_id is None:
        return False

    lead = _fetch_lead_for_queue_item(lead_id)
    if not lead:
        mark_cancelled(queue_id, reason="lead_not_found")
        return False

    status = (lead.get("status") or "").lower().strip()
    if status in {"replied", "interested", "converted", "opt-out", "unsubscribed", "completed", "failed"}:
        mark_cancelled(queue_id, reason=f"stopped_state:{status}")
        return False

    email = (lead.get("email") or "").strip()
    campaign_id = lead.get("campaign_id")

    if not email or campaign_id is None:
        mark_failed(queue_id, retry_after_seconds=3600)
        return False

    try:
        mark_processing(queue_id)

        from outreach_engine.processors.outreach_sender import send_email_async

        sent = await send_email_async(email, int(campaign_id), initial_outreach=False)

        if sent:
            mark_sent(queue_id)
            return True

        mark_failed(queue_id)
        return False

    except Exception as e:
        print(f"⚠️ Queue item failed for lead_id={lead_id}: {e}")
        mark_failed(queue_id)
        return False


async def process_queue_batch(limit: int = 20, concurrency: int = 5) -> List[bool]:
    batch = fetch_next_batch(limit=limit)
    if not batch:
        return []

    semaphore = asyncio.Semaphore(concurrency)

    async def _run(item: Dict[str, Any]) -> bool:
        async with semaphore:
            return await process_queue_item(item)

    tasks = [_run(item) for item in batch]
    return await asyncio.gather(*tasks, return_exceptions=False)


async def run_queue_worker_periodically(
    interval_seconds: int = 30,
    batch_limit: int = 20,
    concurrency: int = 5,
) -> None:
    while True:
        try:
            await process_queue_batch(limit=batch_limit, concurrency=concurrency)
        except Exception as e:
            print(f"⚠️ queue worker error: {e}")
        await asyncio.sleep(interval_seconds)
