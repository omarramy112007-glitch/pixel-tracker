# outreach_engine/api/dashboard.py

from fastapi import APIRouter
from outreach_engine.analytics.dashboard_data import get_campaign_dashboard, get_all_campaigns_dashboard

router = APIRouter()


@router.get("/dashboard/campaigns/{campaign_id}")
async def campaign_insights(campaign_id: int):
    """
    Return campaign metrics + recommendations for a specific campaign
    """
    return get_campaign_dashboard(campaign_id)


@router.get("/dashboard/campaigns")
async def all_campaigns_insights():
    """
    Return metrics + recommendations for all campaigns
    """
    return get_all_campaigns_dashboard()