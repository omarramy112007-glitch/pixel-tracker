# File: outreach_engine/analytics/dashboard_data.py

from outreach_engine.analytics.metrics_calculator import get_metrics, calculate_rates
from outreach_engine.database.supabase_client import supabase
from analytics.revenue_predictor import expected_revenue
from analytics.send_time_predictor import predict_reply_probability

CAMPAIGN_TABLE = "campaigns"

# ---------------------------------------------------
# Generate Recommendations
# ---------------------------------------------------
def generate_recommendations(rates: dict) -> list[str]:
    recommendations = []
    if rates.get("open_rate", 0) < 0.2:
        recommendations.append("Low open rate → consider changing subject lines or sending times.")
    if rates.get("click_rate", 0) > 0.3:
        recommendations.append("High clicks → consider sending demo or value-add emails next.")
    if rates.get("reply_rate", 0) > 0.2:
        recommendations.append("High replies → consider increasing daily send volume.")
    if rates.get("conversion_rate", 0) < 0.05:
        recommendations.append("Low conversions → review offer content or landing page.")
    return recommendations

# ---------------------------------------------------
# Single Campaign Dashboard (Phase 17 AI)
# ---------------------------------------------------
def get_campaign_dashboard(campaign_id: int, channel: str = None) -> dict:
    # Campaign info
    campaign_data = supabase.table(CAMPAIGN_TABLE).select("name").eq("id", campaign_id).execute()
    campaign_name = campaign_data.data[0]["name"] if campaign_data.data else "Unknown Campaign"

    # Metrics & rates
    metrics = get_metrics(campaign_id, channel=channel)
    rates = calculate_rates(metrics)

    # Recommendations
    recommendations = generate_recommendations(rates)

    # Expected revenue + priority_score per lead
    leads = supabase.table("outreach_leads").select("*").eq("campaign_id", campaign_id).execute().data
    total_expected_revenue = 0
    for lead in leads:
        lead["expected_revenue"] = expected_revenue(lead)
        lead["priority_score"] = lead["expected_revenue"] * predict_reply_probability(lead)
        total_expected_revenue += lead["expected_revenue"]
    avg_expected_revenue = total_expected_revenue / len(leads) if leads else 0

    # Dashboard output
    dashboard_data = {
        "campaign_id": campaign_id,
        "campaign_name": campaign_name,
        "emails_sent": metrics.get("email_emails_sent", 0),
        "sms_sent": metrics.get("sms_sent", 0),
        "linkedin_sent": metrics.get("linkedin_sent", 0),
        "calls_made": metrics.get("call_made", 0),
        "opens": metrics.get("email_opens", 0),
        "clicks": metrics.get("email_clicks", 0),
        "replies": metrics.get("email_replies", 0),
        "conversions": metrics.get("email_conversions", 0),
        "open_rate": rates.get("open_rate", 0),
        "click_rate": rates.get("click_rate", 0),
        "reply_rate": rates.get("reply_rate", 0),
        "conversion_rate": rates.get("conversion_rate", 0),
        "recommendations": recommendations,
        "total_expected_revenue": round(total_expected_revenue, 2),
        "avg_expected_revenue": round(avg_expected_revenue, 2)
    }

    return dashboard_data

# ---------------------------------------------------
# All Campaigns Dashboard
# ---------------------------------------------------
def get_all_campaigns_dashboard(channel: str = None) -> list[dict]:
    campaigns = supabase.table(CAMPAIGN_TABLE).select("id").execute().data
    dashboard_list = []
    if campaigns:
        for c in campaigns:
            dashboard_list.append(get_campaign_dashboard(c["id"], channel=channel))
    return dashboard_list