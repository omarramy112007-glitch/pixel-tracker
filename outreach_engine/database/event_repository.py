# File: outreach_engine/database/event_repository.py

from typing import List, Dict, Any, Optional
from datetime import datetime

from outreach_engine.database.supabase_client import supabase


# ---------------------------------------------------
# Store Event
# ---------------------------------------------------
def store_event(
    lead_id: str,
    event_type: str,
    campaign_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Store an engagement event for a lead.
    """

    payload = {
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "event_type": event_type,
        "timestamp": datetime.utcnow().isoformat(),
        "metadata": metadata or {}
    }

    result = (
        supabase
        .table("lead_events")
        .insert(payload)
        .execute()
    )

    return result.data


# ---------------------------------------------------
# Get Events For Lead
# ---------------------------------------------------
def get_events_for_lead(lead_id: str) -> List[Dict[str, Any]]:
    result = (
        supabase
        .table("lead_events")
        .select("*")
        .eq("lead_id", lead_id)
        .order("timestamp", desc=False)
        .execute()
    )
    return result.data


# ---------------------------------------------------
# 🔥 NEW: Get Events For Campaign
# ---------------------------------------------------
def get_events(campaign_id: int) -> List[Dict[str, Any]]:
    result = (
        supabase
        .table("lead_events")
        .select("*")
        .eq("campaign_id", campaign_id)
        .execute()
    )
    return result.data


# ---------------------------------------------------
# Get Last Event
# ---------------------------------------------------
def get_last_event(lead_id: str) -> Optional[Dict[str, Any]]:
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
# 💰 PHASE 15 — DEAL / REVENUE TRACKING
# ===================================================

DEALS_TABLE = "deal_tracking"


# ---------------------------------------------------
# Record Deal
# ---------------------------------------------------
def record_deal(
    lead_id: int,
    campaign_id: int,
    deal_value: float,
    metadata: Optional[Dict[str, Any]] = None
):
    """
    Store deal linked to campaign + lead
    """

    payload = {
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "deal_value": deal_value,
        "created_at": datetime.utcnow().isoformat(),
        "metadata": metadata or {}
    }

    return supabase.table(DEALS_TABLE).insert(payload).execute().data


# ---------------------------------------------------
# Get Campaign Revenue
# ---------------------------------------------------
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