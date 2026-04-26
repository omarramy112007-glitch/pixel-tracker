# outreach_engine/api/campaign_api.py

from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any

from outreach_engine.core.campaign_manager import (
    create_campaign,
    get_active_campaigns,
    pause_campaign,
    resume_campaign,
    assign_leads_to_campaign,
)
from outreach_engine.database.supabase_client import supabase

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])


@router.get("")
@router.get("/")
def list_campaigns():
    """
    Return ALL campaigns for the frontend dropdown.
    """
    try:
        res = (
            supabase.table("campaigns")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/active")
def list_active_campaigns():
    """
    Return active campaigns only.
    """
    try:
        return get_active_campaigns()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{campaign_id}")
def get_campaign(campaign_id: int):
    """
    Return one campaign by id.
    """
    try:
        res = (
            supabase.table("campaigns")
            .select("*")
            .eq("id", campaign_id)
            .limit(1)
            .execute()
        )

        if res.data:
            return res.data[0]

        return {"error": "Campaign not found"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
@router.post("/")
def create_new_campaign(campaign: Dict[str, Any]):
    """
    Create a new campaign.
    """
    try:
        return create_campaign(campaign)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{campaign_id}/pause")
def pause(campaign_id: int):
    try:
        pause_campaign(campaign_id)
        return {"status": "paused", "campaign_id": campaign_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{campaign_id}/resume")
def resume(campaign_id: int):
    try:
        resume_campaign(campaign_id)
        return {"status": "active", "campaign_id": campaign_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{campaign_id}/assign-leads")
def assign_leads(campaign_id: int, leads: List[Dict[str, Any]]):
    """
    Assign leads to a campaign.
    Expects a list of lead dicts.
    """
    try:
        assign_leads_to_campaign(campaign_id, leads)
        return {
            "status": "assigned",
            "campaign_id": campaign_id,
            "lead_count": len(leads),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))