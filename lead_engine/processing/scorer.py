# processing/scorer.py

from typing import Dict, Optional
from lead_engine.core.performance import sync_timer


# -----------------------------
# BASIC SCORE
# -----------------------------
@sync_timer("Basic Score")
def basic_score(
    lead: dict,
    title_weight_map: Optional[Dict[str, float]] = None,
    industry_weight_map: Optional[Dict[str, float]] = None,
) -> float:

    score = 0.0

    title_weight_map = title_weight_map or {}
    industry_weight_map = industry_weight_map or {}

    website = lead.get("website")
    email = lead.get("email")

    # Website
    if website:
        score += 3

    # Country
    country = lead.get("country")
    if isinstance(country, dict):
        country = country.get("name")

    if country == "United States":
        score += 5

    # Industry
    industry = (lead.get("industry") or "").lower()
    if any(k in industry for k in ["saas", "marketing"]):
        score += 5

    # Email
    if email:
        score += 2

    # Title
    title = (lead.get("title") or "").lower()
    if title:
        score += 2

    # Apply weights safely
    for key, weight in industry_weight_map.items():
        if key.lower() in industry:
            score *= weight
            break

    for key, weight in title_weight_map.items():
        if key.lower() in title:
            score *= weight
            break

    return round(score, 2)


# -----------------------------
# AUTOMATION SCORE (FIX)
# -----------------------------
@sync_timer("Automation Score")
def automation_score(lead: dict) -> float:
    """
    Measures how automation-ready the lead/company is
    (simple version just to unblock system)
    """

    score = 0.0

    # Has website → easier automation
    if lead.get("website"):
        score += 2

    # Has tech stack info
    if lead.get("tech_stack"):
        score += 3

    # Has email (critical for outreach)
    if lead.get("email"):
        score += 2

    return score


# -----------------------------
# PERSON SCORE (FIX SAFETY)
# -----------------------------
@sync_timer("Person Score")
def person_score(lead: dict) -> float:
    """
    Measures how valuable the person is (decision maker or not)
    """

    score = 0.0

    title = (lead.get("title") or "").lower()

    if any(k in title for k in ["founder", "ceo", "owner"]):
        score += 5
    elif any(k in title for k in ["head", "director", "manager"]):
        score += 3
    else:
        score += 1

    return score