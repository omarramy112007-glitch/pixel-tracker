# outreach_engine/tracking/pixel_tracker.py

from typing import Optional, Dict, Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import Response

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.engagement_tracking import track_email_open

app = FastAPI(title="Outreach Engine Pixel Tracker")

PIXEL_BYTES = (
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


def _resolve_campaign_id(lead_id: int) -> Optional[int]:
    """
    Try to resolve the campaign_id from outreach_leads using lead_id.
    """
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
        print(f"⚠ Failed to resolve campaign_id for lead {lead_id}: {e}")
    return None


def _build_metadata(request: Request) -> Dict[str, Any]:
    return {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "referer": request.headers.get("referer"),
        "timestamp": None,
        "source": "pixel",
    }


def handle_pixel_open(
    lead_id: int,
    campaign_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Record an email open event from the tracking pixel.
    """
    try:
        resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)

        if not resolved_campaign_id:
            print(f"⚠ Cannot track open: missing campaign_id for lead {lead_id}")
            return

        safe_metadata = metadata or {}

        print(f"💻 Open tracked for lead {lead_id}")
        track_email_open(
            lead_id=lead_id,
            campaign_id=resolved_campaign_id,
            metadata=safe_metadata
        )

        print(f"📬 Email opened | Lead {lead_id} | Campaign {resolved_campaign_id}")

    except Exception as e:
        print(f"❌ Pixel tracking failed: {e}")


@app.get("/pixel")
async def pixel(
    request: Request,
    lead_id: int = Query(..., description="Lead ID for tracking"),
    campaign_id: Optional[int] = Query(None, description="Optional campaign ID"),
):
    """
    Tracking pixel endpoint. Returns 1x1 transparent GIF.
    """
    try:
        handle_pixel_open(
            lead_id=lead_id,
            campaign_id=campaign_id,
            metadata=_build_metadata(request),
        )

        return Response(
            content=PIXEL_BYTES,
            media_type="image/gif",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    except Exception as e:
        print(f"❌ Pixel endpoint error: {e}")
        return Response(status_code=500)


@app.get("/open/{lead_id}")
async def open_pixel(
    lead_id: int,
    request: Request,
    campaign_id: Optional[int] = Query(None, description="Optional campaign ID"),
):
    """
    Backward-compatible open endpoint.
    """
    try:
        handle_pixel_open(
            lead_id=lead_id,
            campaign_id=campaign_id,
            metadata=_build_metadata(request),
        )

        return Response(
            content=PIXEL_BYTES,
            media_type="image/gif",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    except Exception as e:
        print(f"❌ Open endpoint error: {e}")
        return Response(status_code=500)