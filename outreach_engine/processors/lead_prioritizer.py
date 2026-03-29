# outreach_engine/processors/lead_prioritizer.py

from typing import List, Dict
from outreach_engine.core.engagement_scoring import calculate_engagement_score

# ---------------------------------------------------
# Base Weights
# ---------------------------------------------------

WEIGHTS = {
    "base_score": 1.0,
    "automation_score": 1.0,
    "pain_point": 2.0,
    "low_maturity_bonus": 5.0,
    "engagement_score": 1.0,  # NEW: weight for engagement
}


# ---------------------------------------------------
# Calculate Total Priority Score
# ---------------------------------------------------

def calculate_priority_score(lead: Dict) -> float:
    """
    Calculate outreach priority score for a single lead,
    including engagement events.
    """

    base_score = lead.get("score", 0)
    automation_score = lead.get("automation_score", 0)

    # Pain points
    pain_points = lead.get("pain_points") or []
    pain_score = len(pain_points) * WEIGHTS["pain_point"]

    # Automation maturity bonus
    maturity = (lead.get("automation_maturity") or "").lower()
    maturity_bonus = WEIGHTS["low_maturity_bonus"] if maturity == "low" else 0

    # Engagement score from events
    events = lead.get("events", [])
    engagement_score = calculate_engagement_score(events) * WEIGHTS["engagement_score"]

    # Total score
    total = base_score + automation_score + pain_score + maturity_bonus + engagement_score

    # Attach total score to lead for reference
    lead["priority_score"] = total

    return total


# ---------------------------------------------------
# Sort and Return Top Leads
# ---------------------------------------------------

def prioritize_leads(
    leads: List[Dict],
    top_percent: float = 0.3
) -> List[Dict]:
    """
    Sort leads by priority score (including engagement) and return the top percentage.
    """

    if not leads:
        return []

    # Calculate score for each lead
    for lead in leads:
        calculate_priority_score(lead)

    # Sort by priority
    sorted_leads = sorted(
        leads,
        key=lambda l: l.get("priority_score", 0),
        reverse=True
    )

    # Select top %
    cutoff = max(1, int(len(sorted_leads) * top_percent))
    return sorted_leads[:cutoff]