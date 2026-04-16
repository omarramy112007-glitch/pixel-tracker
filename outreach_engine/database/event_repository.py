# File: outreach_engine/database/event_repository.py

from datetime import date, datetime
from typing import List, Dict, Any, Optional

from outreach_engine.database.supabase_client import supabase


# ===================================================
# Utils
# ===================================================

def _json_safe(value: Any) -> Any:
    """
    Make value safe for Supabase JSON storage
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


def _sanitize_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return _json_safe(metadata or {})


def _log(action: str, payload: Dict[str, Any]):
    """
    Unified debug logging for tracking pipeline
    """
    print(f"[EVENT PIPE] {action} → {payload}")


# ===================================================
# Store Event (OPEN / CLICK / REPLY)
# ===================================================

def store_event(
    lead_id: Any,
    event_type: str,
    campaign_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Store engagement event (open / click / reply)
    """

    payload = {
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "event_type": event_type,
        "timestamp": datetime.utcnow().isoformat(),
        "metadata": _sanitize_metadata(metadata)
    }

    try:
        result = (
            supabase
            .table("lead_events")
            .insert(payload)
            .execute()
        )

        data = result.data

        if not data:
            _log("FAILED_INSERT", payload)
            return {}

        _log("EVENT_STORED", payload)
        return data

    except Exception as e:
        _log("SUPABASE_ERROR", {"error": str(e), "payload": payload})
        return {}


# ===================================================
# Get Events For Lead
# ===================================================

def get_events_for_lead(lead_id: Any) -> List[Dict[str, Any]]:
    result = (
        supabase
        .table("lead_events")
        .select("*")
        .eq("lead_id", lead_id)
        .order("timestamp", desc=False)
        .execute()
    )
    return result.data or []


# ===================================================
# Get Events For Campaign
# ===================================================

def get_events(campaign_id: int) -> List[Dict[str, Any]]:
    result = (
        supabase
        .table("lead_events")
        .select("*")
        .eq("campaign_id", campaign_id)
        .execute()
    )
    return result.data or []


# ===================================================
# Get Last Event
# ===================================================

def get_last_event(lead_id: Any) -> Optional[Dict[str, Any]]:
    result = (
        supabase
        .table("lead_events")
        .select("*")
        .eq("lead_id", lead_id)
        .order("timestamp", desc=True)
        .limit(1)
        .execute()
    )

    return result.data[0] if result.data else None


# ===================================================
# 💰 Deal Tracking (Revenue Pipe)
# ===================================================

DEALS_TABLE = "deal_tracking"


def record_deal(
    lead_id: Any,
    campaign_id: int,
    deal_value: float,
    metadata: Optional[Dict[str, Any]] = None
):
    """
    Store deal linked to lead + campaign
    """

    payload = {
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "deal_value": deal_value,
        "created_at": datetime.utcnow().isoformat(),
        "metadata": _sanitize_metadata(metadata)
    }

    try:
        result = supabase.table(DEALS_TABLE).insert(payload).execute()

        if not result.data:
            _log("DEAL_INSERT_FAILED", payload)
            return {}

        _log("DEAL_RECORDED", payload)
        return result.data

    except Exception as e:
        _log("DEAL_SUPABASE_ERROR", {"error": str(e), "payload": payload})
        return {}


def get_campaign_revenue(campaign_id: int):
    result = (
        supabase
        .table(DEALS_TABLE)
        .select("*")
        .eq("campaign_id", campaign_id)
        .execute()
    )

    deals = result.data or []

    total = sum(d.get("deal_value", 0) for d in deals)
    count = len(deals)
    avg = total / count if count else 0

    return {
        "total_revenue": total,
        "deals_count": count,
        "avg_deal_value": avg
    }