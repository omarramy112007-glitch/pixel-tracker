# outreach_engine/api/conversion_tracking.py

from fastapi import FastAPI, Request
from outreach_engine.tracking.engagement_tracking import track_conversion

# Create FastAPI app
app = FastAPI()


@app.post("/event")
async def conversion_event(request: Request):
    """
    Receives conversion events via POST.

    Example payload:
    {
      "lead_id": 123,
      "campaign_id": 1,
      "event_type": "converted",
      "metadata": {...}  # optional
    }
    """

    payload = await request.json()

    lead_id = payload.get("lead_id")
    campaign_id = payload.get("campaign_id")
    event_type = payload.get("event_type")
    metadata = payload.get("metadata", {})

    # Validate payload
    if not lead_id or not campaign_id or event_type != "converted":
        return {"status": "error", "message": "Missing or invalid data"}

    # Record conversion via the tracking system
    track_conversion(campaign_id, lead_id, metadata)

    return {"status": "success", "message": f"Conversion recorded for lead {lead_id}"}