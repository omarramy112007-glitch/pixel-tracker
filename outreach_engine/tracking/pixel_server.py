# outreach_engine/tracking/pixel_server.py

from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Query
from fastapi.responses import Response

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.pixel_tracker import handle_pixel_open

app = FastAPI(title="Outreach Engine Pixel Tracker")

# ---------------------------------------------------
# 1x1 Transparent Tracking Pixel
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


def _resolve_campaign_id(lead_id: int) -> Optional[int]:
    """
    Resolve campaign_id from outreach_leads if not explicitly provided.
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
        print(f"⚠ Failed to resolve campaign_id for pixel open: {e}")
    return None


# ---------------------------------------------------
# GET /open/{lead_id}
# Email open tracking
# ---------------------------------------------------
@app.get("/open/{lead_id}")
async def track_open(
    lead_id: int,
    request: Request,
    campaign_id: Optional[int] = Query(None)
):
    metadata: Dict[str, Any] = {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "referer": request.headers.get("referer"),
    }

    resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)

    if resolved_campaign_id:
        handle_pixel_open(
            lead_id=lead_id,
            campaign_id=resolved_campaign_id,
            metadata=metadata
        )
    else:
        print(
            f"⚠ Pixel open received but campaign_id could not be resolved for lead {lead_id}"
        )

    return Response(
        content=PIXEL,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


# ---------------------------------------------------
# Health Check
# ---------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("outreach_engine.tracking.pixel_server:app", host="0.0.0.0", port=8000)