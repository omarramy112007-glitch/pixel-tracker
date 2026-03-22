# outreach_engine/core/engagement_scoring.py

from typing import List, Dict


# ---------------------------------------------------
# Engagement Score Weights
# ---------------------------------------------------

ENGAGEMENT_WEIGHTS = {
    "opened": 5,
    "clicked": 15,
    "replied": 50
}


# ---------------------------------------------------
# Calculate Engagement Score
# ---------------------------------------------------

def calculate_engagement_score(events: List[Dict]) -> int:
    """
    Calculate engagement score based on recorded events.

    Example:
    open = +5
    click = +15
    reply = +50
    """

    score = 0

    for event in events:

        event_type = event.get("event_type")

        if event_type in ENGAGEMENT_WEIGHTS:
            score += ENGAGEMENT_WEIGHTS[event_type]

    return score


# ---------------------------------------------------
# Update Lead Object With Score
# ---------------------------------------------------

def apply_engagement_score(lead: Dict, events: List[Dict]) -> Dict:
    """
    Attach engagement score to lead object.
    """

    score = calculate_engagement_score(events)

    lead["engagement_score"] = score

    # Optional helpful flags
    lead["email_opened"] = any(e["event_type"] == "opened" for e in events)
    lead["link_clicked"] = any(e["event_type"] == "clicked" for e in events)
    lead["replied"] = any(e["event_type"] == "replied" for e in events)

    return lead