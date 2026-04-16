# outreach_engine/core/gmail_dedup.py

from typing import Optional

from outreach_engine.database.supabase_client import supabase


def _normalize_history_id(history_id: Optional[str]) -> str:
    return (history_id or "").strip()


def is_duplicate(history_id: str) -> bool:
    """
    Returns True if this Gmail history_id was already processed.
    """
    history_id = _normalize_history_id(history_id)
    if not history_id:
        return True

    try:
        res = (
            supabase.table("gmail_history_tracking")
            .select("history_id")
            .eq("history_id", history_id)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception as e:
        print(f"⚠ gmail dedupe check failed for history_id={history_id}: {e}")
        return False


def mark_processed(history_id: str) -> None:
    """
    Marks a Gmail history_id as processed.
    """
    history_id = _normalize_history_id(history_id)
    if not history_id:
        return

    try:
        supabase.table("gmail_history_tracking").insert({
            "history_id": history_id
        }).execute()
    except Exception as e:
        print(f"⚠ failed to mark gmail history_id as processed ({history_id}): {e}")