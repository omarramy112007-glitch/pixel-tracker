# database/tracking.py

from database.supabase_client import supabase


def track_event(lead_id: str, event_type: str):
    """
    Ultra-fast tracking using Supabase RPC (no SELECT needed)

    event_type:
        - open
        - reply
        - meeting
        - deal
    """

    try:
        supabase.rpc("increment_event", {
            "lead_id": lead_id,
            "event_type": event_type
        }).execute()

        print(f"📊 Tracked {event_type} for {lead_id}")

    except Exception as e:
        print(f"❌ Tracking error: {e}")