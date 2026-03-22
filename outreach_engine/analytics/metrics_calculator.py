# analytics/metrics_calculator.py

from outreach_engine.database.supabase_client import supabase
from datetime import date

TABLE_NAME = "campaign_analytics"


# --------------------------------------------------
# Fetch Metrics
# --------------------------------------------------

def get_metrics(campaign_id: int, day: str = None) -> dict:
    """
    Fetch analytics metrics for a campaign.
    day: optional, default today in YYYY-MM-DD format
    """

    if day is None:
        day = str(date.today())

    result = (
        supabase.table(TABLE_NAME)
        .select("*")
        .eq("campaign_id", campaign_id)
        .eq("created_at", day)
        .execute()
    )

    if result.data and len(result.data) > 0:
        return result.data[0]

    return {
        "campaign_id": campaign_id,
        "emails_sent": 0,
        "opens": 0,
        "clicks": 0,
        "replies": 0,
        "conversions": 0,
        "replies_from_followups": 0,
        "created_at": day,
    }


# --------------------------------------------------
# Individual Metric Calculations
# --------------------------------------------------

def calculate_open_rate(opens: int, sent: int) -> float:
    if sent == 0:
        return 0
    return round((opens / sent) * 100, 2)


def calculate_click_rate(clicks: int, sent: int) -> float:
    if sent == 0:
        return 0
    return round((clicks / sent) * 100, 2)


def calculate_reply_rate(replies: int, sent: int) -> float:
    if sent == 0:
        return 0
    return round((replies / sent) * 100, 2)


def calculate_conversion_rate(conversions: int, sent: int) -> float:
    if sent == 0:
        return 0
    return round((conversions / sent) * 100, 2)


def calculate_followup_effectiveness(
    replies_from_followups: int,
    total_replies: int
) -> float:
    """
    Measure how many replies came from follow-ups
    """

    if total_replies == 0:
        return 0

    return round((replies_from_followups / total_replies) * 100, 2)


# --------------------------------------------------
# Combined Metrics Engine
# --------------------------------------------------

def calculate_rates(metrics: dict) -> dict:
    """
    Calculate all campaign metrics together
    """

    emails_sent = metrics.get("emails_sent", 0)
    opens = metrics.get("opens", 0)
    clicks = metrics.get("clicks", 0)
    replies = metrics.get("replies", 0)
    conversions = metrics.get("conversions", 0)
    followup_replies = metrics.get("replies_from_followups", 0)

    return {
        "open_rate": calculate_open_rate(opens, emails_sent),
        "click_rate": calculate_click_rate(clicks, emails_sent),
        "reply_rate": calculate_reply_rate(replies, emails_sent),
        "conversion_rate": calculate_conversion_rate(conversions, emails_sent),
        "followup_effectiveness": calculate_followup_effectiveness(
            followup_replies,
            replies
        ),
    }