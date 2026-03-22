# outreach_engine/tracking/event_api.py

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import RedirectResponse, Response, JSONResponse
from typing import Optional, Dict, Any
import base64

from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase


app = FastAPI(title="Outreach Engine Tracking API")


# ---------------------------------------------------
# Transparent 1x1 pixel
# ---------------------------------------------------

PIXEL_BASE64 = b"R0lGODlhAQABAPAAAP///wAAACH5BAAAAAAALAAAAAABAAEAAAICRAEAOw=="
PIXEL_BYTES = base64.b64decode(PIXEL_BASE64)


# ---------------------------------------------------
# Helper: Update lead engagement flags
# ---------------------------------------------------

def update_lead_flags(lead_id: str, updates: Dict[str, Any]):

    try:
        (
            supabase
            .table("leads")
            .update(updates)
            .eq("id", lead_id)
            .execute()
        )
    except Exception as e:
        print(f"⚠ Lead update failed: {e}")


# ---------------------------------------------------
# POST /event
# Generic event endpoint
# ---------------------------------------------------

@app.post("/event")
async def track_event(payload: Dict[str, Any]):

    lead_id = payload.get("lead_id")
    event_type = payload.get("event_type")
    metadata = payload.get("metadata", {})

    if not lead_id or not event_type:
        raise HTTPException(
            status_code=400,
            detail="lead_id and event_type required"
        )

    try:

        store_event(
            lead_id=lead_id,
            event_type=event_type,
            metadata=metadata
        )

        updates = {}

        if event_type == "opened":
            updates["email_opened"] = True

        elif event_type == "clicked":
            updates["link_clicked"] = True

        elif event_type == "replied":
            updates["replied"] = True

        elif event_type == "sent":
            updates["sent"] = True

        elif event_type == "failed":
            updates["failed"] = True

        if updates:
            update_lead_flags(lead_id, updates)

        return JSONResponse({
            "status": "success",
            "event": event_type
        })

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"event tracking failed: {str(e)}"
        )


# ---------------------------------------------------
# GET /open/{lead_id}
# Email open tracking pixel
# ---------------------------------------------------

@app.get("/open/{lead_id}")
async def track_open(lead_id: str, request: Request):

    try:

        metadata = {
            "ip": request.client.host,
            "user_agent": request.headers.get("user-agent")
        }

        store_event(
            lead_id=lead_id,
            event_type="opened",
            metadata=metadata
        )

        update_lead_flags(lead_id, {
            "email_opened": True
        })

        return Response(
            content=PIXEL_BYTES,
            media_type="image/gif"
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"open tracking failed: {str(e)}"
        )


# ---------------------------------------------------
# GET /click/{lead_id}
# Link click tracking + redirect
# ---------------------------------------------------

@app.get("/click/{lead_id}")
async def track_click(
    lead_id: str,
    request: Request,
    url: Optional[str] = Query(None)
):

    try:

        metadata = {
            "url": url,
            "ip": request.client.host,
            "user_agent": request.headers.get("user-agent")
        }

        store_event(
            lead_id=lead_id,
            event_type="clicked",
            metadata=metadata
        )

        update_lead_flags(lead_id, {
            "link_clicked": True
        })

        if url:
            return RedirectResponse(url)

        return JSONResponse({
            "status": "clicked recorded"
        })

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"click tracking failed: {str(e)}"
        )


# ---------------------------------------------------
# Health Check
# ---------------------------------------------------

@app.get("/health")
async def health():

    return {
        "status": "ok"
    }