# outreach_engine/api/funnel_api.py

from fastapi import FastAPI
from outreach_engine.analytics.campaign_analytics import get_campaign_funnel

app = FastAPI()

@app.get("/analytics/campaign/{campaign_id}/funnel")
def campaign_funnel(campaign_id: int):
    """
    Returns the conversion funnel for a given campaign:
      first email → replied → converted
    """
    funnel_data = get_campaign_funnel(campaign_id)
    return funnel_data