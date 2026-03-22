# File: outreach_engine/api/revenue_api.py

from fastapi import APIRouter
from outreach_engine.database.event_repository import get_campaign_revenue, record_deal
from outreach_engine.analytics.revenue_analytics import weekly_revenue
from outreach_engine.alerts.alerts_manager import check_quota_and_delivery

router = APIRouter()

# Record a deal
@router.post("/deal")
def create_deal(lead_id: int, campaign_id: int, deal_value: float):
    record_deal(lead_id, campaign_id, deal_value)
    return {"status": "ok", "lead_id": lead_id, "deal_value": deal_value}

# Get revenue metrics for campaign
@router.get("/campaign/{campaign_id}/revenue")
def campaign_revenue(campaign_id: int):
    return get_campaign_revenue(campaign_id)

# Weekly revenue
@router.get("/campaign/{campaign_id}/revenue/weekly")
def campaign_weekly_revenue(campaign_id: int):
    return weekly_revenue(campaign_id)

# Alerts
@router.get("/campaign/{campaign_id}/alerts")
def campaign_alerts(campaign_id: int):
    alerts = check_quota_and_delivery(campaign_id)
    return {"campaign_id": campaign_id, "alerts": alerts}