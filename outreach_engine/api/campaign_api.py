# outreach_engine/api/campaign_api.py

from fastapi import APIRouter
from typing import List

from outreach_engine.core.campaign_manager import (
    create_campaign,
    get_active_campaigns,
    pause_campaign,
    resume_campaign,
    assign_leads_to_campaign
)

router = APIRouter()


# --------------------------------------------------
# GET /campaigns
# --------------------------------------------------

@router.get("/")
def list_campaigns():
    return get_active_campaigns()


# --------------------------------------------------
# GET /campaigns/{id}
# --------------------------------------------------

@router.get("/{campaign_id}")
def get_campaign(campaign_id: int):
    campaigns = get_active_campaigns()
    for c in campaigns:
        if c["id"] == campaign_id:
            return c

    return {"error": "Campaign not found"}


# --------------------------------------------------
# POST /campaigns
# --------------------------------------------------

@router.post("/")
def create_new_campaign(campaign: dict):
    return create_campaign(campaign)


# --------------------------------------------------
# POST /campaigns/{id}/pause
# --------------------------------------------------

@router.post("/{campaign_id}/pause")
def pause(campaign_id: int):
    pause_campaign(campaign_id)
    return {"status": "paused", "campaign_id": campaign_id}


# --------------------------------------------------
# POST /campaigns/{id}/resume
# --------------------------------------------------

@router.post("/{campaign_id}/resume")
def resume(campaign_id: int):
    resume_campaign(campaign_id)
    return {"status": "active", "campaign_id": campaign_id}


# --------------------------------------------------
# POST /campaigns/{id}/assign-leads
# --------------------------------------------------

@router.post("/{campaign_id}/assign-leads")
def assign_leads(campaign_id: int, leads: List[int]):
    assign_leads_to_campaign(campaign_id, leads)
    return {
        "status": "assigned",
        "campaign_id": campaign_id,
        "lead_count": len(leads)
    }