# outreach_engine/tracking/pixel_server.py

from fastapi import FastAPI, Request
from fastapi.responses import Response

from outreach_engine.database.event_repository import store_event


# Create FastAPI app
app = FastAPI()


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


# ---------------------------------------------------
# GET /open/{lead_id}
# Email open tracking
# ---------------------------------------------------

@app.get("/open/{lead_id}")
async def track_open(lead_id: str, request: Request):

    metadata = {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "referer": request.headers.get("referer")
    }

    store_event(
        lead_id=lead_id,
        event_type="opened",
        metadata=metadata
    )

    return Response(
        content=PIXEL,
        media_type="image/gif"
    )