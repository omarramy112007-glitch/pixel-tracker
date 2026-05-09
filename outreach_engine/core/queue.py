# outreach_engine/core/queue.py

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from outreach_engine.database.supabase_client import supabase

MAX_RETRY_COUNT = 5
MAX_RESCHEDULE_SECONDS = 24 * 60 * 60  # 24 hours


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def add_lead_to_queue(lead_id: int, followup_step: int = 0, delay_hours: int = 0):
    """
    Insert a lead into the outreach queue.
    """
    scheduled_at = _now_utc() + timedelta(hours=delay_hours)
    payload = {
        "lead_id": lead_id,
        "followup_step": followup_step,
        "status": "pending",
        "scheduled_at": scheduled_at.isoformat(),
        "retry_count": 0,
        "created_at": _now_utc().isoformat(),
        "last_attempt": None,
        "last_error": None,
    }
    return supabase.table("outreach_queue").insert(payload).execute()


def fetch_next_batch(limit: int = 20):
    """
    Get the next batch of leads ready to send.
    """
    try:
        response = (
            supabase.table("outreach_queue")
            .select("*")
            .eq("status", "pending")
            .lte("scheduled_at", _now_utc().isoformat())
            .order("scheduled_at", ascending=True)
            .limit(limit)
            .execute()
        )
        return response.data or []
    except Exception as e:
        print(f"⚠️ fetch_next_batch error: {e}")
        return []


def mark_sent(queue_id: int):
    """
    Mark a queued item as sent.
    """
    return (
        supabase.table("outreach_queue")
        .update(
            {
                "status": "sent",
                "last_attempt": _now_utc().isoformat(),
                "last_error": None,
            }
        )
        .eq("id", queue_id)
        .execute()
    )


def mark_failed(queue_id: int, retry_after_seconds: Optional[int] = None):
    """
    Mark a queued item as failed and reschedule it if retries remain.
    If retry_after_seconds is given, it is used as the next delay.
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
                "last_attempt": _now_utc().isoformat(),
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
                "last_attempt": _now_utc().isoformat(),
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


def mark_rate_limited(queue_id: int, retry_after_seconds: Optional[int] = None):
    """
    Alias for mark_failed used when Gmail returns 429.
    """
    return mark_failed(queue_id, retry_after_seconds=retry_after_seconds)