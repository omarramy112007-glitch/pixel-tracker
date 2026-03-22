# File: outreach_engine/analytics/revenue_analytics.py

from datetime import datetime, timedelta
from outreach_engine.database.supabase_client import supabase


# ---------------------------------------------------
# Core Revenue Function
# ---------------------------------------------------
def get_campaign_revenue(campaign_id: int, since: datetime = None):
    """
    Returns revenue metrics for a campaign
    Optional: filter by date (since)
    """

    query = supabase.table("deals").select("*").eq("campaign_id", campaign_id)

    if since:
        query = query.gte("created_at", since.isoformat())

    deals = query.execute().data

    total = sum(d["value"] for d in deals if d["status"] == "won")
    count = len([d for d in deals if d["status"] == "won"])

    return {
        "total_revenue": total,
        "deals_closed": count,
        "avg_deal_value": total / count if count else 0
    }


# ---------------------------------------------------
# Weekly Revenue
# ---------------------------------------------------
def weekly_revenue(campaign_id: int):
    """
    Calculate revenue per week for a campaign
    """
    today = datetime.utcnow()
    week_start = today - timedelta(days=today.weekday())

    return get_campaign_revenue(campaign_id, since=week_start)