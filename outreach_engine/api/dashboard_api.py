# File: outreach_engine/api/dashboard_api.py

from fastapi import FastAPI, Query, HTTPException
from outreach_engine.analytics.dashboard_data import get_campaign_dashboard

app = FastAPI()


@app.get("/dashboard/campaigns/{campaign_id}")
def campaign_dashboard(campaign_id: int, channel: str = Query(default="")):
    """
    Returns campaign metrics filtered by channel if provided.

    channel:
    - email
    - sms
    - linkedin
    - call
    - "" (all)
    """

    try:
        data = get_campaign_dashboard(campaign_id, channel=channel)

        return {
            "campaign_id": campaign_id,
            "channel": channel or "all",
            "data": data
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))