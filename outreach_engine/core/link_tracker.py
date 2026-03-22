# outreach_engine/core/link_tracker.py

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from typing import Optional

# Import the store_event function from your database module
from outreach_engine.database.event_repository import store_event

# This **must** be called `app`
app = FastAPI()


@app.get("/click/{lead_id}")
async def track_click(
    lead_id: str,
    url: Optional[str] = None,
    request: Request = None
):
    metadata = {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "url": url
    }

    store_event(
        lead_id=lead_id,
        event_type="clicked",
        metadata=metadata
    )

    if url:
        return RedirectResponse(url)

    return {"status": "click recorded"}