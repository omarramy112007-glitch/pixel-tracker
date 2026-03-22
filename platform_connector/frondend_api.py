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
    await connector.update_leads()
    return connector.get_leads()


# -----------------------------
# Run Campaign (Background)
# -----------------------------
@app.post("/campaign/run")
async def api_run_campaign(
    background_tasks: BackgroundTasks,
    run_full: bool = Query(False)
):
    """
    run_full = true → full autopilot (with follow-ups)
    run_full = false → initial outreach only
    """
    # Add the campaign task to background so the request doesn't block
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
    return {"status": "ok"}