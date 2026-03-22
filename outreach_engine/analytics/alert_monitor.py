# File: outreach_engine/analytics/alert_monitor.py

from outreach_engine.analytics.dashboard_data import get_all_campaigns_dashboard
from outreach_engine.alerts.alert_dispatcher import trigger_alert

# Thresholds
HIGH_REPLY_RATE = 0.3       # >30% replies triggers spike
LOW_OPEN_RATE = 0.15        # <15% opens triggers low engagement
SMTP_FAILURES_THRESHOLD = 5 # Example for failures

# Mock storage for SMTP failures
smtp_failures = {}  # {campaign_id: count}

def check_campaigns_alerts():
    campaigns = get_all_campaigns_dashboard()
    for c in campaigns:
        campaign_id = c["campaign_id"]

        # High reply spike
        if c.get("reply_rate", 0) > HIGH_REPLY_RATE:
            trigger_alert(
                "High reply spike",
                campaign_id,
                f"Reply rate is {c['reply_rate']*100:.1f}%"
            )

        # Low engagement
        if c.get("open_rate", 0) < LOW_OPEN_RATE:
            trigger_alert(
                "Low engagement",
                campaign_id,
                f"Open rate is {c['open_rate']*100:.1f}%"
            )

        # SMTP failures
        failures = smtp_failures.get(campaign_id, 0)
        if failures >= SMTP_FAILURES_THRESHOLD:
            trigger_alert(
                "SMTP failures exceeded threshold",
                campaign_id,
                f"Failures: {failures}"
            )