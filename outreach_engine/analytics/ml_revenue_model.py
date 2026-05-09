# outreach_engine/analytics/ml_revenue_model.py

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Dict, Optional

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT_DIR / "models" / "xgb_revenue_model.pkl"

ml_model = None


def _load_model() -> Optional[object]:
    global ml_model

    if ml_model is not None:
        return ml_model

    try:
        with MODEL_PATH.open("rb") as f:
            ml_model = pickle.load(f)
            print(f"✅ ML revenue model loaded: {MODEL_PATH}")
            return ml_model
    except FileNotFoundError:
        ml_model = None
        print("⚠ ML model not found. Using fallback revenue predictor.")
    except Exception as e:
        ml_model = None
        print(f"⚠ Failed to load ML model ({MODEL_PATH}): {e}. Using fallback revenue predictor.")

    return ml_model


def predict_revenue_ml(lead: Dict) -> float:
    """
    Predict expected revenue for a single lead using ML.
    Falls back to standard expected_revenue if the model is missing.
    """
    model = _load_model()

    if model is None:
        from outreach_engine.analytics.revenue_predictor import expected_revenue
        return float(expected_revenue(lead))

    company_size_map = {"small": 1, "medium": 2, "large": 3}
    role = str(lead.get("role", "") or "").lower().strip()

    features = np.array(
        [
            float(lead.get("engagement_score", 0) or 0),
            company_size_map.get(str(lead.get("company_size", "small") or "small").lower(), 1),
            float(lead.get("deal_value", 0) or 0),
            5 if role in {"ceo", "cto", "founder", "manager"} else 2,
            float(lead.get("touch_count", 1) or 1),
            float(lead.get("avg_response_hours", 48) or 48),
        ],
        dtype=float,
    ).reshape(1, -1)

    try:
        predicted = float(model.predict(features)[0])
        return max(predicted, 0.0)
    except Exception as e:
        print(f"⚠ ML prediction failed: {e}. Falling back to expected_revenue.")
        from outreach_engine.analytics.revenue_predictor import expected_revenue
        return float(expected_revenue(lead))