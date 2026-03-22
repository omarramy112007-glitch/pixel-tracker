# outreach_engine/analytics/revenue_prediction.py

from typing import Dict
from analytics.lead_scoring import calculate_engagement_score
from database.supabase_client import supabase

def predict_reply_probability(lead: dict) -> float:
    """
    Predict probability that a lead will reply.
    Example: simple ML / heuristic based on engagement score
    """
    score = calculate_engagement_score(lead)
    # Normalize to 0-1 probability
    probability = min(max(score / 100, 0), 1)
    return probability

def predict_deal_value(lead: dict) -> float:
    """
    Predict expected deal value based on historical deals & lead attributes
    """
    # Average of past deals from similar leads
    past_deals = supabase.table("deals").select("*") \
        .eq("lead_id", lead.get("id")) \
        .execute().data

    avg_value = 0
    if past_deals:
        won_deals = [d["value"] for d in past_deals if d["status"] == "won"]
        if won_deals:
            avg_value = sum(won_deals) / len(won_deals)

    # Optionally combine with company size / industry weighting
    size_weight = {"small": 0.8, "medium": 1, "large": 1.2}
    industry_weight = {"tech": 1.2, "finance": 1, "other": 0.9}

    weighted_value = avg_value * size_weight.get(lead.get("company_size", "small"), 1) \
                     * industry_weight.get(lead.get("industry", "other"), 1)

    return weighted_value

def expected_revenue(lead: dict) -> float:
    """
    Calculate expected revenue for lead:
    probability of reply * predicted deal value
    """
    prob = predict_reply_probability(lead)
    value = predict_deal_value(lead)
    return prob * value