# outreach_engine/analytics/revenue_predictor.py

from typing import Dict
from outreach_engine.analytics.send_time_predictor import predict_reply_probability

# ---------------------------------------------------
# Predict Deal Value (AI-ready baseline)
# ---------------------------------------------------
def predict_deal_value(lead: Dict) -> float:
    """
    Estimate deal value BEFORE conversion.

    Uses:
    - company size
    - industry
    - optional historical hints

    Easily replaceable with ML model later.
    """

    # Base value
    base_value = 100.0

    # ---------------- Company Size ----------------
    size_multiplier = {
        "small": 1.0,
        "medium": 2.0,
        "large": 4.0
    }

    # ---------------- Industry ----------------
    industry_multiplier = {
        "tech": 2.0,
        "finance": 1.8,
        "health": 1.5,
        "other": 1.0
    }

    size = (lead.get("company_size") or "small").lower()
    industry = (lead.get("industry") or "other").lower()

    value = base_value
    value *= size_multiplier.get(size, 1.0)
    value *= industry_multiplier.get(industry, 1.0)

    # ---------------- Optional historical boost ----------------
    # If lead already had a deal before
    if lead.get("deal_value"):
        value = max(value, float(lead["deal_value"]))

    return float(round(value, 2))


# ---------------------------------------------------
# Expected Revenue
# ---------------------------------------------------
def calculate_expected_revenue(lead: Dict) -> float:
    """
    expected_revenue = reply_probability * predicted_deal_value
    """

    probability = predict_reply_probability(lead)
    predicted_value = predict_deal_value(lead)

    expected = probability * predicted_value

    return float(round(expected, 2))


# ---------------------------------------------------
# Full Revenue Prediction Package
# ---------------------------------------------------
def enrich_lead_with_revenue(lead: Dict) -> Dict:
    """
    Adds:
    - predicted_deal_value
    - reply_probability
    - expected_revenue
    """

    try:
        predicted_value = predict_deal_value(lead)
        probability = predict_reply_probability(lead)
        expected = probability * predicted_value

        lead["predicted_deal_value"] = float(round(predicted_value, 2))
        lead["reply_probability"] = float(round(probability, 4))
        lead["expected_revenue"] = float(round(expected, 2))

    except Exception as e:
        # Fail-safe: never break pipeline
        print(f"⚠ Revenue prediction failed: {e}")
        lead["predicted_deal_value"] = 0.0
        lead["reply_probability"] = 0.0
        lead["expected_revenue"] = 0.0

    return lead


# ---------------------------------------------------
# Batch Processing
# ---------------------------------------------------
def enrich_leads_batch(leads: list[Dict]) -> list[Dict]:
    """
    Enrich multiple leads with revenue predictions.
    """
    return [enrich_lead_with_revenue(lead) for lead in leads]


# ---------------------------------------------------
# Sorting Helper (🔥 money-first prioritization)
# ---------------------------------------------------
def sort_leads_by_revenue(leads: list[Dict]) -> list[Dict]:
    """
    Sort leads by expected revenue descending.
    """
    return sorted(
        leads,
        key=lambda l: l.get("expected_revenue", 0),
        reverse=True
    )


# ---------------------------------------------------
# Alias for backward compatibility
# ---------------------------------------------------
expected_revenue = calculate_expected_revenue