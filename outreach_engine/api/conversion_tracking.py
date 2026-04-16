# outreach_engine/api/conversion_tracking.py

from fastapi import FastAPI, Request
from outreach_engine.tracking.event_repository import store_event

app = FastAPI()


@app.post("/event")
async def conversion_event(request: Request):
    """
    Receives conversion events via POST.

    Expected payload:
    {
      "lead_id": 123,
      "campaign_id": 1,
      "metadata": {...}  # optional
    }
    """

    payload = await request.json()

    lead_id = payload.get("lead_id")
    campaign_id = payload.get("campaign_id")
    metadata = payload.get("metadata") or {}

    # hard validation
    if not lead_id or not campaign_id:
        return {
            "status": "error",
            "message": "Missing lead_id or campaign_id"
        }

    # force correct event type
    result = store_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="converted",
        metadata=metadata
    )

    return {
        "status": "success",
        "message": f"Conversion recorded for lead {lead_id}",
        "result": result
    }