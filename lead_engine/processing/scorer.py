# processing/scoring.py

from typing import Dict, Optional
from lead_engine.core.performance import sync_timer


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