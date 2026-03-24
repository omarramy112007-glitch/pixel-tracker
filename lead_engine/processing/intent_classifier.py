#lead_engine/processors/intent_classifier.py

from typing import Dict


# --------------------------------------------------
# 🧠 Intent Scoring (Rule-Based - قابل للتطوير لـ ML)
# --------------------------------------------------

def score_intent(lead: Dict) -> float:
    """
    Calculate intent score (0 → 1) based on lead signals.

    This is rule-based for now but designed to be easily replaced
    with ML model predictions later.
    """

    score = 0.0

    # -------------------------
    # Strong Buying Signals
    # -------------------------
    if lead.get("has_budget"):
        score += 0.3

    if lead.get("decision_maker"):
        score += 0.25

    # -------------------------
    # Pain & Need Signals
    # -------------------------
    pain_points = lead.get("pain_points", [])
    if pain_points:
        score += min(0.2, 0.05 * len(pain_points))  # more pains → slightly higher score

    # -------------------------
    # Engagement Signals
    # -------------------------
    engagement = lead.get("engagement_score", 0)

    if engagement >= 0.7:
        score += 0.25
    elif engagement >= 0.4:
        score += 0.15
    elif engagement > 0:
        score += 0.05

    # -------------------------
    # Company Size Signal (optional)
    # -------------------------
    company_size = lead.get("company_size")

    if company_size:
        if company_size >= 50:
            score += 0.1
        elif company_size >= 10:
            score += 0.05

    # -------------------------
    # Clamp score بين 0 و 1
    # -------------------------
    return round(min(score, 1.0), 2)


# --------------------------------------------------
# 🏷 Lead Classification
# --------------------------------------------------

def classify_lead(lead: Dict) -> Dict:
    """
    Adds:
    - intent_score
    - category (agency / consulting)
    - priority_tag (hot / warm / cold)
    """

    intent_score = score_intent(lead)

    lead["intent_score"] = intent_score

    # -------------------------
    # Category
    # -------------------------
    if intent_score >= 0.7:
        lead["category"] = "consulting"
    else:
        lead["category"] = "agency"

    # -------------------------
    # Priority Tag (useful later)
    # -------------------------
    if intent_score >= 0.8:
        lead["priority_tag"] = "hot"
    elif intent_score >= 0.5:
        lead["priority_tag"] = "warm"
    else:
        lead["priority_tag"] = "cold"

    return lead


# --------------------------------------------------
# 🔄 Batch Processing
# --------------------------------------------------

def classify_leads(leads: list) -> list:
    """
    Apply classification to a list of leads
    """
    return [classify_lead(lead) for lead in leads]


# --------------------------------------------------
# 🚀 Future Hook (ML Ready)
# --------------------------------------------------

def score_intent_ml(lead: Dict) -> float:
    """
    Placeholder for ML-based intent scoring.

    Example future:
    return model.predict(lead_features)
    """
    return score_intent(lead)