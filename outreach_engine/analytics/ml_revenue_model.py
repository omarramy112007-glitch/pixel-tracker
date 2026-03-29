# File: outreach_engine/analytics/ml_revenue_model.py

import pickle
import numpy as np
from typing import Dict

# Load pre-trained XGBoost/LightGBM model
try:
    with open("models/xgb_revenue_model.pkl", "rb") as f:
        ml_model = pickle.load(f)
except FileNotFoundError:
    ml_model = None
    print("⚠ ML model not found. Using fallback revenue predictor.")

def predict_revenue_ml(lead: Dict) -> float:
    """
    Predict expected revenue for a single lead using ML.
    Falls back to standard expected_revenue if model is missing.
    """
    if ml_model is None:
        from outreach_engine.analytics.revenue_predictor import expected_revenue
        return expected_revenue(lead)

    # Feature engineering
    features = [
        lead.get("engagement_score", 0),
        {"small":1, "medium":2, "large":3}.get(lead.get("company_size","small"), 1),
        lead.get("deal_value", 0),
        5 if lead.get("role","").lower() in ["ceo","cto","founder","manager"] else 2,
        lead.get("touch_count", 1),
        lead.get("avg_response_hours", 48)
    ]
    features = np.array(features).reshape(1, -1)

    predicted = ml_model.predict(features)[0]
    return max(predicted, 0)  # Ensure revenue is non-negative