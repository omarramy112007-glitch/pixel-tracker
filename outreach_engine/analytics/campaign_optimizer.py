# File: outreach_engine/analytics/campaign_optimizer.py

from typing import List, Dict
from outreach_engine.analytics.ml_revenue_model import predict_revenue_ml
from outreach_engine.analytics.pricing_optimizer import adjust_pricing

MAX_LEADS_PER_BATCH = 50  # Limit leads per campaign batch

def optimize_campaign(leads: List[Dict]) -> List[Dict]:
    """
    Phase 18 Ultra AI Optimization:
    - Calculate ML revenue
    - Adjust pricing per lead
    - Filter low-potential leads
    - Rank by priority_score (ML revenue)
    """

    # ✅ Step 1: Add ML revenue & pricing
    for lead in leads:
        lead["ml_revenue"] = predict_revenue_ml(lead)
        lead["price"] = adjust_pricing(lead)
        lead["priority_score"] = lead["ml_revenue"]  # Use ML revenue as main metric

    # ✅ Step 2: Filter out low-potential leads (example: ML revenue < 50)
    filtered = [l for l in leads if l["ml_revenue"] > 50]

    # ✅ Step 3: Sort descending by priority_score
    optimized = sorted(filtered, key=lambda l: l["priority_score"], reverse=True)

    # ✅ Step 4: Limit total leads per batch
    return optimized[:MAX_LEADS_PER_BATCH]