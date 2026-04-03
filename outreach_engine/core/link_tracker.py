# outreach_engine/core/link_tracker.py

from fastapi import FastAPI, Request, Query
from fastapi.responses import RedirectResponse, JSONResponse
from typing import Optional
from urllib.parse import unquote

from outreach_engine.database.event_repository import store_event

# This must be called app
app = FastAPI(title="Outreach Engine Link Tracker")


@app.get("/click/{lead_id}")
async def track_click(
    lead_id: str,
    request: Request,
    url: Optional[str] = Query(default=None),
    campaign_id: Optional[int] = Query(default=None),
):
    """
    Track link clicks and optionally redirect to destination URL.
    """
    decoded_url = unquote(url) if url else None

    metadata = {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "url": decoded_url,
        "campaign_id": campaign_id,
    }

    try:
        store_event(
            lead_id=lead_id,
            event_type="clicked",
            campaign_id=campaign_id,
            metadata=metadata,
        )
    except Exception as e:
        print(f"⚠ Click tracking failed: {e}")

    if decoded_url:
        return RedirectResponse(decoded_url)

    return JSONResponse({"status": "click recorded", "lead_id": lead_id})