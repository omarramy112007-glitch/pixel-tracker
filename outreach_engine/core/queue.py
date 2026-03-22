# outreach_engine/core/queue.py

from datetime import datetime, timedelta
from database.supabase_client import supabase

def add_lead_to_queue(lead_id: int, followup_step: int = 0, delay_hours: int = 0):
    """
    Insert a lead into the outreach queue.
    """
    scheduled_at = datetime.utcnow() + timedelta(hours=delay_hours)
    payload = {
        "lead_id": lead_id,
        "followup_step": followup_step,
        "status": "Pending",
        "scheduled_at": scheduled_at,
        "retry_count": 0,
        "created_at": datetime.utcnow()
    }
    supabase.table("outreach_queue").insert(payload).execute()


def fetch_next_batch(limit: int = 20):
    """
    Get the next batch of leads ready to send.
    """
    try:
        response = (
            supabase
            .table("outreach_queue")
            .select("*")
            .eq("status", "Pending")
            .lte("scheduled_at", datetime.utcnow().isoformat())
            .order("scheduled_at", ascending=True)
            .limit(limit)
            .execute()
        )
        return response.data or []
    except Exception as e:
        print(f"⚠️ fetch_next_batch error: {e}")
        return []


def mark_sent(queue_id: int):
    supabase.table("outreach_queue").update({
        "status": "Sent",
        "last_attempt": datetime.utcnow()
    }).eq("id", queue_id).execute()


def mark_failed(queue_id: int):
    supabase.table("outreach_queue").update({
        "status": "Failed",
        "last_attempt": datetime.utcnow(),
        "retry_count": supabase.table("outreach_queue").select("retry_count").eq("id", queue_id).execute().data[0]["retry_count"] + 1
    }).eq("id", queue_id).execute()