# File: outreach_engine/analytics/dashboard_data.py

from typing import Optional, Dict, List
from outreach_engine.analytics.metrics_calculator import get_metrics as original_get_metrics
from outreach_engine.analytics.analytics_routers import get_campaigns_list  # افترضنا فيه دالة تجيب كل الحملات

# ---------------------------------------------------
# Wrapper for metrics to accept channel argument
# ---------------------------------------------------
def get_metrics(campaign_id: int, channel: Optional[str] = None) -> Dict:
    """
    Fetch campaign metrics, optionally filtered by channel.
    """
    metrics = original_get_metrics(campaign_id)

    if channel:
        # افترضنا إن metrics فيها keys زي "email", "sms", "linkedin", "call"
        filtered = {channel: metrics.get(channel, {})}
        return filtered

    return metrics


# ---------------------------------------------------
# Campaign Dashboard
# ---------------------------------------------------
def get_campaign_dashboard(campaign_id: int, channel: Optional[str] = None) -> Dict:
    """
    Return dashboard for a single campaign.
    Supports optional channel filtering.
    """
    try:
        metrics = get_metrics(campaign_id, channel=channel)
        return {
            "campaign_id": campaign_id,
            "metrics": metrics
        }
    except Exception as e:
        print(f"⚠ Failed to get campaign dashboard: {e}")
        return {}


# ---------------------------------------------------
# All Campaigns Dashboard
# ---------------------------------------------------
def get_all_campaigns_dashboard(channel: Optional[str] = None) -> List[Dict]:
    """
    Return dashboards for all campaigns.
    """
    dashboards = []
    campaigns = get_campaigns_list()  # رجعنا list of dicts with 'id'

    for c in campaigns:
        dashboards.append(get_campaign_dashboard(c["id"], channel=channel))

    return dashboards