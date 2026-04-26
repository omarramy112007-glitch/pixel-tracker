# outreach_engine/core/link_tracker.py

from urllib.parse import unquote
from typing import Optional

from fastapi import FastAPI, Request, Query
from fastapi.responses import RedirectResponse, JSONResponse

from outreach_engine.database.event_repository import store_event

app = FastAPI(title="Outreach Engine Link Tracker")


def _record_click(
    lead_id: int,
    request: Request,
    campaign_id: Optional[int] = None,
    url: Optional[str] = None
):
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

    return decoded_url


@app.get("/click/{lead_id}")
async def track_click(
    lead_id: int,
    request: Request,
    url: Optional[str] = Query(default=None),
    campaign_id: Optional[int] = Query(default=None),
):
    decoded_url = _record_click(
        lead_id=lead_id,
        request=request,
        campaign_id=campaign_id,
        url=url,
    )

    if decoded_url:
        return RedirectResponse(decoded_url)

    return JSONResponse({"status": "click recorded", "lead_id": lead_id})


@app.get("/track/click")
async def track_click_legacy(
    request: Request,
    lead_id: int = Query(...),
    url: Optional[str] = Query(default=None),
    campaign_id: Optional[int] = Query(default=None),
):
    decoded_url = _record_click(
        lead_id=lead_id,
        request=request,
        campaign_id=campaign_id,
        url=url,
    )

    if decoded_url:
        return RedirectResponse(decoded_url)

    return JSONResponse({"status": "click recorded", "lead_id": lead_id})