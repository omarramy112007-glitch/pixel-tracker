#outreach_engine/analytics/ml_model.py

import joblib
import pandas as pd
from xgboost import XGBRegressor
from outreach_engine.database.supabase_client import supabase

MODEL_PATH = "models/revenue_model.pkl"

# ------------------------------------------
# Prepare Data
# ------------------------------------------
def fetch_training_data():
    data = supabase.table("outreach_leads").select("*").execute().data
    return pd.DataFrame(data)

def preprocess(df: pd.DataFrame):
    df = df.fillna(0)

    features = [
        "email_opened",
        "link_clicked",
        "touch_count",
        "avg_response_hours",
        "deal_value"
    ]

    X = df[features]
    y = df["deal_value"]  # target = revenue

    return X, y

# ------------------------------------------
# Train Model
# ------------------------------------------
def train_model():
    df = fetch_training_data()
    X, y = preprocess(df)

    model = XGBRegressor(n_estimators=100, max_depth=5)
    model.fit(X, y)

    joblib.dump(model, MODEL_PATH)
    print("✅ Model trained and saved")

# ------------------------------------------
# Predict Revenue
# ------------------------------------------
def predict_revenue_ml(lead: dict):
    model = joblib.load(MODEL_PATH)

    X = pd.DataFrame([{
        "email_opened": lead.get("email_opened", 0),
        "link_clicked": lead.get("link_clicked", 0),
        "touch_count": lead.get("touch_count", 1),
        "avg_response_hours": lead.get("avg_response_hours", 48),
        "deal_value": lead.get("deal_value", 0),
    }])

    return float(model.predict(X)[0])