# outreach_engine/api/admin_api.py

from fastapi import FastAPI

from outreach_engine.api.campaign_api import router as campaign_router
from outreach_engine.api.analytics_api import router as analytics_router
from outreach_engine.api.conversion_tracking import app as conversion_app

app = FastAPI(title="Outreach Engine Admin API")


# --------------------------------------------------
# Campaign Control API
# --------------------------------------------------

app.include_router(campaign_router, prefix="/campaigns", tags=["Campaigns"])


# --------------------------------------------------
# Analytics API
# --------------------------------------------------

app.include_router(analytics_router, prefix="/analytics", tags=["Analytics"])


# --------------------------------------------------
# Conversion Tracking (already built)
# --------------------------------------------------

app.mount("", conversion_app)


@app.get("/")
def root():
    return {
        "service": "Outreach Engine Admin API",
        "status": "running"
    }