# File: platform_connector/frontend_api.py

from fastapi import FastAPI, BackgroundTasks, Query, HTTPException
from platform_connector.connector import PlatformConnector

app = FastAPI(title="Platform Connector API")
connector = PlatformConnector()


async def _run_campaign_job(run_full: bool = False):
    """
    Background job:
    1) refresh leads
    2) run campaign
    """
    await connector.update_leads()
    return await connector.run_campaign(run_full=run_full)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/leads")
async def get_leads():
    try:
        await connector.update_leads()
        leads = connector.get_leads()
        return {
            "status": "success",
            "count": len(leads),
            "leads": leads,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/campaign/run")
async def run_campaign(
    background_tasks: BackgroundTasks,
    run_full: bool = Query(
        False,
        description="True = full autopilot, False = initial outreach only",
    ),
):
    background_tasks.add_task(_run_campaign_job, run_full)
    return {
        "status": "started",
        "mode": "full" if run_full else "initial",
    }


@app.post("/campaign/run-now")
async def run_campaign_now(run_full: bool = Query(False)):
    try:
        result = await _run_campaign_job(run_full)
        return {
            "status": "completed",
            "mode": "full" if run_full else "initial",
            "result": result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("platform_connector.api:app", host="127.0.0.1", port=8000, reload=True)