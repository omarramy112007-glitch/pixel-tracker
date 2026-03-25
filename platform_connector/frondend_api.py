# File: platform_connector/frontend_api.py

from fastapi import FastAPI, Query, BackgroundTasks
from platform_connector.connector import PlatformConnector

app = FastAPI()
connector = PlatformConnector()


# -----------------------------
# Get Leads
# -----------------------------
@app.get("/leads")
async def api_get_leads():
    """
    Fetch the latest leads from the platform connector.
    """
    await connector.update_leads()
    return connector.get_leads()


# -----------------------------
# Run Campaign (Background)
# -----------------------------
@app.post("/campaign/run")
async def api_run_campaign(
    background_tasks: BackgroundTasks,
    run_full: bool = Query(False, description="True → full autopilot with follow-ups; False → initial outreach only")
):
    """
    Starts a campaign run in the background.
    run_full = True → full autopilot (with follow-ups)
    run_full = False → initial outreach only
    """
    # Schedule the campaign to run in the background
    background_tasks.add_task(connector.run_campaign, run_full)

    # Immediate response to frontend
    return {
        "status": "started",
        "mode": "full" if run_full else "initial"
    }


# -----------------------------
# Health Check
# -----------------------------
@app.get("/health")
def health_check():
    """
    Basic health check endpoint.
    """
    return {"status": "ok"}