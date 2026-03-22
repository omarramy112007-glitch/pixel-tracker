# analytics/crm_analytics.py

from outreach_engine.database.supabase_client import supabase
from datetime import datetime

TABLE_NAME = "crm_analytics"


def update_crm_metrics(
    lead_id: int,
    emails_sent: int = 0,
    opens: int = 0,
    clicks: int = 0,
    replies: int = 0,
    conversions: int = 0,
    last_activity: datetime = None
):
    """
    Updates or inserts CRM analytics metrics for a lead.
    """
    if last_activity is None:
        last_activity = datetime.utcnow()

    # Check if lead already exists
    existing = supabase.table(TABLE_NAME).select("*").eq("lead_id", lead_id).execute()

    if existing.data and len(existing.data) > 0:
        # Update existing record
        supabase.table(TABLE_NAME).update({
            "emails_sent": existing.data[0]["emails_sent"] + emails_sent,
            "opens": existing.data[0]["opens"] + opens,
            "clicks": existing.data[0]["clicks"] + clicks,
            "replies": existing.data[0]["replies"] + replies,
            "conversions": existing.data[0]["conversions"] + conversions,
            "last_activity": last_activity
        }).eq("lead_id", lead_id).execute()
    else:
        # Insert new record
        supabase.table(TABLE_NAME).insert({
            "lead_id": lead_id,
            "emails_sent": emails_sent,
            "opens": opens,
            "clicks": clicks,
            "replies": replies,
            "conversions": conversions,
            "last_activity": last_activity
        }).execute()


def compute_engagement_score(metrics: dict) -> float:
    """
    Example scoring: weighted sum
    Emails sent: 1pt
    Opens: 2pt
    Clicks: 3pt
    Replies: 5pt
    Conversions: 10pt
    """
    score = (
        metrics.get("emails_sent", 0) * 1 +
        metrics.get("opens", 0) * 2 +
        metrics.get("clicks", 0) * 3 +
        metrics.get("replies", 0) * 5 +
        metrics.get("conversions", 0) * 10
    )
    return score