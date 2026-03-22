# File: outreach_engine/api/optimization_api.py

from fastapi import FastAPI
from outreach_engine.ai.optimization_engine import analyze_campaign

app = FastAPI()

@app.get("/analytics/campaign/{campaign_id}/optimize")
def optimize_campaign(campaign_id: int):
    """
    Returns AI insights + recommendations for campaign optimization
    """
    return analyze_campaign(campaign_id)