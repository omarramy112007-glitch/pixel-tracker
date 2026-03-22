#outreach_engine/analytics/pricing_engine.py
def adjust_pricing(lead: dict):
    base_value = lead.get("deal_value", 100)

    # High intent → increase price
    if lead.get("priority_score", 0) > 500:
        return base_value * 1.2

    # Medium → keep
    elif lead.get("priority_score", 0) > 200:
        return base_value

    # Low → discount
    else:
        return base_value * 0.8