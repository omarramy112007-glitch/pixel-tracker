# outreach_engine/tracking/event_repository.py

from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional

from outreach_engine.database.supabase_client import supabase


def _json_safe(value: Any) -> Any:
    """
    Recursively convert non-JSON-serializable values into safe values.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    return value


def log_event(
    lead_id: int,
    campaign_id: Optional[int],
    event_type: str,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Central event logger for the entire system.

    event_type examples:
    - sent
    - opened
    - clicked
    - replied
    - converted
    - ai_action
    - rl_decision
    """
    payload = {
        "lead_id": lead_id,
        "event_type": event_type,
        "timestamp": datetime.utcnow().isoformat(),
        "metadata": _json_safe(metadata or {}),
    }

    if campaign_id is not None:
        payload["campaign_id"] = campaign_id

    try:
        res = supabase.table("lead_events").insert(payload).execute()
        return {"status": "success", "data": res.data}

    except Exception:
        # fallback for schemas that don't include campaign_id
        if "campaign_id" in payload:
            fallback_payload = dict(payload)
            fallback_payload.pop("campaign_id", None)

            try:
                res = supabase.table("lead_events").insert(fallback_payload).execute()
                return {"status": "success", "data": res.data}
            except Exception as e2:
                return {"status": "error", "message": str(e2)}

        return {"status": "error", "message": "failed to insert event"}


def store_event(
    lead_id: int,
    event_type: str,
    campaign_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return log_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type=event_type,
        metadata=metadata
    )


def get_lead_events(lead_id: int) -> List[Dict[str, Any]]:
    try:
        res = (
            supabase.table("lead_events")
            .select("*")
            .eq("lead_id", lead_id)
            .order("timestamp", desc=True)
            .execute()
        )
        return res.data or []
    except Exception:
        return []


def get_campaign_events(campaign_id: int) -> List[Dict[str, Any]]:
    try:
        res = (
            supabase.table("lead_events")
            .select("*")
            .eq("campaign_id", campaign_id)
            .order("timestamp", desc=True)
            .execute()
        )
        return res.data or []
    except Exception:
        return []


def count_events(
    campaign_id: int,
    event_type: Optional[str] = None
) -> int:
    try:
        query = supabase.table("lead_events").select("*").eq("campaign_id", campaign_id)

        if event_type:
            query = query.eq("event_type", event_type)

        res = query.execute()
        return len(res.data or [])

    except Exception:
        return 0


def log_ai_action(
    lead: Dict[str, Any],
    action: str,
    priority_score: float,
    reply_probability: float,
    predicted_revenue: float
):
    return log_event(
        lead_id=lead["id"],
        campaign_id=lead.get("campaign_id"),
        event_type="ai_action",
        metadata={
            "action": action,
            "priority_score": priority_score,
            "reply_probability": reply_probability,
            "predicted_revenue": predicted_revenue
        }
    )


def log_rl_decision(lead: Dict[str, Any], action: str):
    return log_event(
        lead_id=lead["id"],
        campaign_id=lead.get("campaign_id"),
        event_type="rl_decision",
        metadata={"action": action}
    )


def log_conversion(lead_id: int, campaign_id: int, revenue: float):
    return log_event(
        lead_id,
        campaign_id,
        "converted",
        {"revenue": revenue}
    )


def log_email_sent(lead_id: int, campaign_id: int):
    return log_event(lead_id, campaign_id, "sent")


def log_email_opened(lead_id: int, campaign_id: int):
    return log_event(lead_id, campaign_id, "opened")


def log_link_clicked(lead_id: int, campaign_id: int):
    return log_event(lead_id, campaign_id, "clicked")


def log_reply(lead_id: int, campaign_id: int):
    return log_event(lead_id, campaign_id, "replied")


def delete_old_events(days: int = 90):
    try:
        cutoff_dt = datetime.utcnow() - timedelta(days=days)
        cutoff_iso = cutoff_dt.isoformat()

        res = (
            supabase.table("lead_events")
            .delete()
            .lt("timestamp", cutoff_iso)
            .execute()
        )
        return res.data

    except Exception as e:
        return {"error": str(e)}