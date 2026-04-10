# platform_connector/api.py

import asyncio
from fastapi import FastAPI, Query, HTTPException
from platform_connector.connector import PlatformConnector

app = FastAPI(title="Platform Connector API")
connector = PlatformConnector()


async def _run_campaign_job(run_full: bool = False):
    print("🔥 JOB STARTED")

    try:
        print("➡️ Fetching leads...")
        await connector.update_leads()
        leads = connector.get_leads()
        print(f"✅ Leads fetched: {len(leads)}")

        print("➡️ Running campaign...")
        result = await connector.run_campaign(run_full=run_full)
        print(f"✅ Campaign result: {result}")

        return {
            "status": "completed",
            "mode": "full" if run_full else "initial",
            "leads_fetched": len(leads),
            "result": result,
        }

    except Exception as e:
        print(f"❌ ERROR IN JOB: {e}")
        return {
            "status": "error",
            "error": str(e)
        }


@app.get("/")
def root():
    print("🔥 ROOT HIT")
    return {
        "message": "API is working",
    }


@app.get("/health")
def health_check():
    print("🔥 HEALTH HIT")
    return {"status": "ok"}


@app.get("/leads")
async def get_leads():
    print("🔥 LEADS ENDPOINT HIT")

    try:
        await connector.update_leads()
        leads = connector.get_leads()

        print(f"✅ Leads count: {len(leads)}")

        return {
            "status": "success",
            "count": len(leads),
            "leads": leads,
        }

    except Exception as e:
        print(f"❌ LEADS ERROR: {e}")
        return {
            "status": "error",
            "error": str(e)
        }


@app.api_route("/campaign/run", methods=["GET", "POST"])
async def run_campaign(run_full: bool = Query(False)):
    print("🔥 RUN (background) HIT")

    try:
        asyncio.create_task(_run_campaign_job(run_full))

        return {
            "status": "started",
            "mode": "full" if run_full else "initial",
        }

    except Exception as e:
        print(f"❌ RUN ERROR: {e}")
        return {
            "status": "error",
            "error": str(e)
        }


@app.api_route("/campaign/run-now", methods=["GET", "POST"])
async def run_campaign_now(run_full: bool = Query(False)):
    print("🔥 RUN-NOW HIT")

    result = await _run_campaign_job(run_full)

    print("🔥 RESPONSE RETURNED:", result)

    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("platform_connector.api:app", host="127.0.0.1", port=8000, reload=True)