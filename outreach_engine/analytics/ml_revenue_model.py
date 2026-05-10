# outreach_engine/analytics/ml_revenue_model.py

from __future__ import annotations

from typing import Dict


def predict_revenue_ml(lead: Dict) -> float:
    """
    Predict expected revenue for a single lead.

    Pickle/model loading has been removed completely.
    This now always falls back to the standard revenue predictor.
    """
    from outreach_engine.analytics.revenue_predictor import expected_revenue

    try:
        return float(expected_revenue(lead))
    except Exception as e:
        print(f"⚠ Revenue fallback failed: {e}")
        return 0.0