# File: outreach_engine/alerts/alert_manager.py

from outreach_engine.analytics.campaign_analytics import get_real_time_metrics
from outreach_engine.core.retry import retry
from outreach_engine.core.performance import timer

DAILY_QUOTA = 500
DELIVERY_THRESHOLD = 80.0  # percent


@retry
@timer("Check Quota and Delivery")
def check_quota_and_delivery(campaign_id: int) -> list:
    """
    Check campaign for quota and delivery thresholds, returns list of alert messages.
    """
    metrics = get_real_time_metrics(campaign_id) or {}
    alerts = []

    if metrics.get("emails_sent", 0) >= DAILY_QUOTA:
        alerts.append(f"⚠ Daily quota reached: {metrics.get('emails_sent')} emails sent.")

    if metrics.get("open_rate", 0) < DELIVERY_THRESHOLD:
        alerts.append(f"⚠ Low engagement: Open rate at {metrics.get('open_rate')}%.")

    return alerts