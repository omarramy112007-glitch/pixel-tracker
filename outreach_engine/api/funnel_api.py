# File: outreach_engine/api/funnel_api.py

from fastapi import FastAPI, HTTPException

from outreach_engine.analytics.campaign_analytics import get_campaign_funnel

app = FastAPI(title="Outreach Engine Funnel API")


@app.get("/analytics/campaign/{campaign_id}/funnel")
def campaign_funnel(campaign_id: int):
    """
    Returns the conversion funnel for a given campaign:
      sent → opened → clicked → replied → converted
    """
    try:
        return get_campaign_funnel(campaign_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))