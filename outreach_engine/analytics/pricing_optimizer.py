# File: outreach_engine/analytics/pricing_optimizer.py

from typing import Dict

BASE_PRICE = 100  # Default base price

def adjust_pricing(lead: Dict, base_price: float = BASE_PRICE) -> float:
    """
    Dynamically adjust price per lead based on predicted revenue, engagement, and urgency.
    Higher priority_score or predicted revenue → higher price.
    """
    # Use ML revenue if available, else expected revenue
    revenue = lead.get("ml_revenue") or lead.get("expected_revenue") or base_price

    touch_count = lead.get("touch_count", 1)
    urgency_score = lead.get("urgency_score", 1)  # Optional lead metric
    priority_score = lead.get("priority_score", 0)

    # Pricing formula
    multiplier = 1 + min(priority_score / 100, 0.5)  # cap multiplier at 1.5x
    price = revenue * (0.8 + 0.2 * touch_count) * urgency_score * multiplier
    price = round(max(price, 10), 2)  # Minimum price = 10

    return price