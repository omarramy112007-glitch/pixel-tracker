# File: outreach_engine/analytics/campaign_analytics.py

from datetime import datetime
from outreach_engine.database.supabase_client import supabase
from outreach_engine.core.lead_manager import get_campaign_leads

TABLE_NAME = "campaign_analytics"

# --------------------------------------------------
# Internal helper to update metrics
# --------------------------------------------------
def _update_metric(campaign_id: int, column: str, increment: int = 1):
    today = datetime.utcnow().date()
    existing = (
        supabase.table(TABLE_NAME)
        .select("*")
        .eq("campaign_id", campaign_id)
        .eq("created_at", str(today))
        .execute()
    )

    if existing.data and len(existing.data) > 0:
        row_id = existing.data[0]["id"]
        supabase.table(TABLE_NAME).update({
            column: existing.data[0].get(column, 0) + increment
        }).eq("id", row_id).execute()
    else:
        supabase.table(TABLE_NAME).insert({
            "campaign_id": campaign_id,
            "emails_sent": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
            "emails_per_provider": {},
            "created_at": today,
            column: increment
        }).execute()


# --------------------------------------------------
# Event Recording
# --------------------------------------------------
def record_email_sent(campaign_id: int, lead_id: int = None):
    _update_metric(campaign_id, "emails_sent")
    if lead_id:
        supabase.table("outreach_leads").update({
            "status": "sent",
            "first_contacted_at": datetime.utcnow().isoformat()
        }).eq("id", lead_id).execute()

def record_email_provider(campaign_id: int, provider: str):
    """
    Track emails sent per provider.
    """
    today = datetime.utcnow().date()
    existing = supabase.table(TABLE_NAME).select("*") \
        .eq("campaign_id", campaign_id).eq("created_at", str(today)).execute()

    if existing.data and len(existing.data) > 0:
        row = existing.data[0]
        providers = row.get("emails_per_provider", {})
        providers[provider] = providers.get(provider, 0) + 1
        supabase.table(TABLE_NAME).update({"emails_per_provider": providers}).eq("id", row["id"]).execute()
    else:
        supabase.table(TABLE_NAME).insert({
            "campaign_id": campaign_id,
            "emails_sent": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
            "emails_per_provider": {provider: 1},
            "created_at": today
        }).execute()


def record_open(campaign_id: int, lead_id: int = None):
    _update_metric(campaign_id, "opens")
    if lead_id:
        supabase.table("outreach_leads").update({"email_opened": True}).eq("id", lead_id).execute()

def record_click(campaign_id: int, lead_id: int = None):
    _update_metric(campaign_id, "clicks")
    if lead_id:
        supabase.table("outreach_leads").update({"link_clicked": True}).eq("id", lead_id).execute()

def record_reply(campaign_id: int, lead_id: int = None):
    _update_metric(campaign_id, "replies")
    if lead_id:
        supabase.table("outreach_leads").update({
            "status": "replied",
            "replied_at": datetime.utcnow().isoformat()
        }).eq("id", lead_id).execute()

def record_conversion(campaign_id: int, lead_id: int = None):
    _update_metric(campaign_id, "conversions")
    if lead_id:
        supabase.table("outreach_leads").update({
            "status": "converted",
            "converted_at": datetime.utcnow().isoformat()
        }).eq("id", lead_id).execute()


# --------------------------------------------------
# Real-time Metrics
# --------------------------------------------------
def get_real_time_metrics(campaign_id: int):
    leads = get_campaign_leads(campaign_id)
    metrics = {
        "emails_sent": sum(1 for l in leads if l.get("status") in ["sent","replied","converted"]),
        "opens": sum(1 for l in leads if l.get("email_opened")),
        "clicks": sum(1 for l in leads if l.get("link_clicked")),
        "replies": sum(1 for l in leads if l.get("status") == "replied"),
        "conversions": sum(1 for l in leads if l.get("status") == "converted")
    }

    # Rates
    metrics["open_rate"] = round((metrics["opens"]/metrics["emails_sent"]*100),1) if metrics["emails_sent"] else 0
    metrics["click_through_rate"] = round((metrics["clicks"]/metrics["emails_sent"]*100),1) if metrics["emails_sent"] else 0
    metrics["reply_rate"] = round((metrics["replies"]/metrics["emails_sent"]*100),1) if metrics["emails_sent"] else 0
    metrics["conversion_rate"] = round((metrics["conversions"]/metrics["emails_sent"]*100),1) if metrics["emails_sent"] else 0

    return metrics


# --------------------------------------------------
# Funnel Analysis
# --------------------------------------------------
def get_campaign_funnel(campaign_id: int) -> dict:
    leads = get_campaign_leads(campaign_id)
    total_sent = sum(1 for l in leads if l.get("status") in ["sent","replied","converted"])
    replied = sum(1 for l in leads if l.get("status") == "replied")
    converted = sum(1 for l in leads if l.get("status") == "converted")

    drop_off_reply = ((total_sent - replied)/total_sent*100) if total_sent else 0
    drop_off_conversion = ((replied - converted)/replied*100) if replied else 0

    return {
        "total_sent": total_sent,
        "replied": replied,
        "converted": converted,
        "drop_off_to_reply_pct": round(drop_off_reply,1),
        "drop_off_to_conversion_pct": round(drop_off_conversion,1)
    }


# --------------------------------------------------
# Lead & Campaign Engagement
# --------------------------------------------------
def get_lead_engagement_rate(lead):
    score = lead.get("engagement_score",0)
    return min(score/10,1.0)

def get_campaign_engagement(campaign_id: int):
    leads = get_campaign_leads(campaign_id)
    rates = [get_lead_engagement_rate(l) for l in leads]
    return sum(rates)/len(rates) if rates else 0


# --------------------------------------------------
# ROI / Deals vs Emails
# --------------------------------------------------
def calculate_campaign_roi(campaign_id: int):
    metrics = get_real_time_metrics(campaign_id)
    emails_sent = metrics.get("emails_sent",1)
    conversions = metrics.get("conversions",0)
    return conversions/emails_sent


# --------------------------------------------------
# Follow-up Effectiveness
# --------------------------------------------------
def followup_effectiveness(campaign_id: int):
    from outreach_engine.database.event_repository import get_events
    events = get_events(campaign_id)
    steps = {}
    for e in events:
        step = e.get("metadata",{}).get("step",0)
        if step not in steps:
            steps[step] = {"sent":0,"replied":0}
        if e["event_type"]=="sent":
            steps[step]["sent"] += 1
        if e["event_type"]=="replied":
            steps[step]["replied"] += 1
    for step in steps:
        s = steps[step]["sent"]
        r = steps[step]["replied"]
        steps[step]["conversion"] = r/s if s else 0
    return steps


# --------------------------------------------------
# Alerts
# --------------------------------------------------
def check_delivery_alert(campaign_id: int, threshold: float=0.8):
    metrics = get_real_time_metrics(campaign_id)
    sent = metrics.get("emails_sent",0)
    delivered = metrics.get("opens",0)
    delivery_rate = delivered/sent if sent else 1.0
    if delivery_rate < threshold:
        print(f"⚠ Delivery rate low: {delivery_rate*100:.1f}% for campaign {campaign_id}")
        return True
    return False