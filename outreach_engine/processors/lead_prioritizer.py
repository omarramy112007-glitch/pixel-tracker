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
    "engagement_score": 1.0,
}


# ---------------------------------------------------
# Helpers
# ---------------------------------------------------
def _as_number(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_events(lead: Dict) -> list:
    events = lead.get("events", [])
    if isinstance(events, list):
        return events
    return []


# ---------------------------------------------------
# Calculate Total Priority Score
# ---------------------------------------------------
def calculate_priority_score(lead: Dict) -> float:
    """
    Calculate outreach priority score for a single lead,
    including engagement events.
    """

    base_score = _as_number(lead.get("score", 0)) * WEIGHTS["base_score"]
    automation_score = _as_number(lead.get("automation_score", 0)) * WEIGHTS["automation_score"]

    pain_points = lead.get("pain_points") or []
    if not isinstance(pain_points, list):
        pain_points = [pain_points]
    pain_score = len(pain_points) * WEIGHTS["pain_point"]

    maturity = (lead.get("automation_maturity") or "").lower()
    maturity_bonus = WEIGHTS["low_maturity_bonus"] if maturity == "low" else 0

    events = _normalize_events(lead)
    engagement_score = _as_number(calculate_engagement_score(events)) * WEIGHTS["engagement_score"]

    total = base_score + automation_score + pain_score + maturity_bonus + engagement_score

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

    for lead in leads:
        calculate_priority_score(lead)

    sorted_leads = sorted(
        leads,
        key=lambda l: _as_number(l.get("priority_score", 0)),
        reverse=True
    )

    cutoff = max(1, int(len(sorted_leads) * top_percent))
    return sorted_leads[:cutoff]