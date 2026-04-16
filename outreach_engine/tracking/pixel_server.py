# outreach_engine/tracking/pixel_server.py

from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Query
from fastapi.responses import Response

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import store_event

app = FastAPI(title="Outreach Engine Pixel Tracker")

# ---------------------------------------------------
# 1x1 Pixel
# ---------------------------------------------------
PIXEL = (
    b"GIF89a"
    b"\x01\x00\x01\x00"
    b"\x80\x00\x00"
    b"\x00\x00\x00"
    b"\xff\xff\xff"
    b"!\xf9\x04"
    b"\x01\x00\x00\x00\x00"
    b",\x00\x00\x00\x00"
    b"\x01\x00\x01\x00"
    b"\x00\x02\x02"
    b"D\x01\x00;"
)

# ---------------------------------------------------
# Helpers
# ---------------------------------------------------
def _resolve_campaign_id(lead_id: int) -> Optional[int]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("campaign_id")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("campaign_id")
    except Exception as e:
        print(f"⚠ campaign resolve error: {e}")
    return None


def _pixel_response():
    return Response(
        content=PIXEL,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _track_open(lead_id: int, campaign_id: Optional[int], metadata: Dict[str, Any]):
    try:
        print(f"🔥 PIXEL HIT → Lead {lead_id}")

        store_event(
            lead_id=lead_id,
            event_type="open",
            campaign_id=campaign_id,
            metadata=metadata
        )

        # IMPORTANT: also update lead + analytics tables
        supabase.table("outreach_leads").update({
            "email_opened": True,
            "email_opened_at": datetime.utcnow().isoformat(),
        }).eq("id", lead_id).execute()

        supabase.table("crm_analytics").upsert({
            "lead_id": lead_id,
            "opens": 1,
            "last_opened_at": datetime.utcnow().isoformat(),
        }).execute()

        print(f"📬 OPEN TRACKED → Lead {lead_id}")

    except Exception as e:
        print(f"❌ Tracking failed: {e}")


# ---------------------------------------------------
# Routes
# ---------------------------------------------------
@app.get("/open/{lead_id}")
async def open_pixel(
    lead_id: int,
    request: Request,
    campaign_id: Optional[int] = Query(None),
):
    metadata = {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "ts": datetime.utcnow().isoformat(),
        "channel": "email",
        "source": "pixel",
    }

    resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)
    _track_open(lead_id, resolved_campaign_id, metadata)

    return _pixel_response()


@app.get("/pixel")
async def pixel(
    lead_id: int = Query(...),
    request: Request = None,
):
    metadata = {
        "ip": request.client.host if request and request.client else None,
        "user_agent": request.headers.get("user-agent") if request else None,
        "ts": datetime.utcnow().isoformat(),
        "channel": "email",
        "source": "pixel",
    }

    campaign_id = _resolve_campaign_id(lead_id)
    _track_open(lead_id, campaign_id, metadata)

    return _pixel_response()


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------
# Run
# ---------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "outreach_engine.tracking.pixel_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )