# outreach_engine/tracking/pixel_server.py

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import Response

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import store_event

app = FastAPI(title="Outreach Engine Pixel Tracker")

PROCESS_LOCK = asyncio.Lock()
OPEN_CACHE: dict[int, float] = {}
OPEN_DEDUP_SECONDS = 30

BASE_DIR = Path(__file__).resolve().parents[2]

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


def _open_event_key(lead_id: int, day_str: Optional[str] = None) -> str:
    if not day_str:
        day_str = datetime.utcnow().date().isoformat()
    return f"{lead_id}:open:{day_str}"


def _open_already_recorded(lead_id: int) -> bool:
    """
    Dedupe opens by lead per day.
    """
    day_key = _open_event_key(lead_id)

    try:
        res = (
            supabase.table("lead_events")
            .select("id")
            .eq("event_key", day_key)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception as e:
        print(f"⚠ open dedupe check failed: {e}")
        return False


# ---------------------------------------------------
# 🔥 CORE FIX: OPEN TRACKING
# ---------------------------------------------------
async def _track_open(lead_id: int, campaign_id: Optional[int], metadata: Dict[str, Any]):
    try:
        now_ts = datetime.utcnow().timestamp()

        # In-memory cooldown to stop repeated pixel hits
        last_seen = OPEN_CACHE.get(lead_id)
        if last_seen and (now_ts - last_seen) < OPEN_DEDUP_SECONDS:
            print(f"🧠 Duplicate open ignored (cooldown) → Lead {lead_id}")
            return

        # DB-level dedupe for the whole day
        if _open_already_recorded(lead_id):
            print(f"🧠 Duplicate open ignored (DB) → Lead {lead_id}")
            OPEN_CACHE[lead_id] = now_ts
            return

        OPEN_CACHE[lead_id] = now_ts

        print(f"🔥 PIXEL HIT → Lead {lead_id}")

        # 1) Log event
        open_date = datetime.utcnow().date().isoformat()
        store_event(
            lead_id=lead_id,
            event_type="open",
            campaign_id=campaign_id,
            metadata={**metadata, "open_date": open_date},
        )

        now = datetime.utcnow().isoformat()

        # 2) Get current open count
        res = (
            supabase.table("outreach_leads")
            .select("open_count, email_opened")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        current_open_count = 0
        already_opened = False

        if res.data:
            row = res.data[0]
            current_open_count = row.get("open_count") or 0
            already_opened = bool(row.get("email_opened"))

        # 3) Update lead state
        update_payload = {
            "open_count": current_open_count + 1,
            "email_opened": True,
            "last_updated": now,
        }

        if not already_opened:
            update_payload["email_opened_at"] = now

        supabase.table("outreach_leads").update(update_payload).eq("id", lead_id).execute()

        print(f"📬 OPEN TRACKED → Lead {lead_id} | count={current_open_count + 1}")

        # 4) Update CRM analytics safely
        try:
            crm_res = (
                supabase.table("crm_analytics")
                .select("opens")
                .eq("lead_id", lead_id)
                .limit(1)
                .execute()
            )

            if crm_res.data:
                current_opens = int(crm_res.data[0].get("opens") or 0)
                update_crm = {
                    "opens": current_opens + 1,
                    "last_opened_at": now,
                    "last_activity": now,
                }

                try:
                    supabase.table("crm_analytics").update(update_crm).eq("lead_id", lead_id).execute()
                except Exception:
                    update_crm.pop("last_opened_at", None)
                    supabase.table("crm_analytics").update(update_crm).eq("lead_id", lead_id).execute()
            else:
                payload = {
                    "lead_id": lead_id,
                    "engagement_score": 0,
                    "emails_sent": 0,
                    "opens": 1,
                    "clicks": 0,
                    "replies": 0,
                    "conversions": 0,
                    "last_activity": now,
                    "last_opened_at": now,
                }

                try:
                    supabase.table("crm_analytics").insert(payload).execute()
                except Exception:
                    payload.pop("last_opened_at", None)
                    supabase.table("crm_analytics").insert(payload).execute()

        except Exception as e:
            print(f"⚠ crm_analytics skipped: {e}")

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
    async with PROCESS_LOCK:
        metadata = {
            "ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "ts": datetime.utcnow().isoformat(),
            "channel": "email",
            "source": "pixel",
        }

        resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)
        await _track_open(lead_id, resolved_campaign_id, metadata)

        return _pixel_response()


@app.get("/pixel")
async def pixel(
    lead_id: int = Query(...),
    request: Request = None,
):
    async with PROCESS_LOCK:
        metadata = {
            "ip": request.client.host if request and request.client else None,
            "user_agent": request.headers.get("user-agent") if request else None,
            "ts": datetime.utcnow().isoformat(),
            "channel": "email",
            "source": "pixel",
        }

        campaign_id = _resolve_campaign_id(lead_id)
        await _track_open(lead_id, campaign_id, metadata)

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