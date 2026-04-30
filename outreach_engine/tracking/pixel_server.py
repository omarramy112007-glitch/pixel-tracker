from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Query
from fastapi.responses import Response, RedirectResponse

from outreach_engine.database.supabase_client import supabase
from outreach_engine.database.event_repository import store_event

app = FastAPI(title="Outreach Engine Pixel Tracker")

PROCESS_LOCK = asyncio.Lock()

OPEN_CACHE: dict[int, float] = {}
CLICK_CACHE: dict[int, float] = {}

OPEN_DEDUP_SECONDS = 900   # 15 minutes
CLICK_DEDUP_SECONDS = 300  # 5 minutes

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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _pixel_response() -> Response:
    return Response(
        content=PIXEL,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _safe_headers(request: Optional[Request]) -> Dict[str, Any]:
    if not request:
        return {"ip": None, "user_agent": None, "referer": None}

    return {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "referer": request.headers.get("referer"),
    }


def _is_browser_like(user_agent: Optional[str]) -> bool:
    if not user_agent:
        return False

    ua = user_agent.lower()

    blocked = (
        "curl",
        "wget",
        "httpie",
        "python-requests",
        "aiohttp",
        "okhttp",
        "postmanruntime",
        "insomnia",
        "go-http-client",
        "powershell",
    )
    if any(x in ua for x in blocked):
        return False

    if "mozilla" not in ua and "googleimageproxy" not in ua:
        return False

    return True


def _recent_event_exists(lead_id: int, cache: dict[int, float], cooldown_seconds: int) -> bool:
    now_ts = _utc_now().timestamp()
    last_seen = cache.get(lead_id)

    if last_seen and (now_ts - last_seen) < cooldown_seconds:
        return True

    cache[lead_id] = now_ts
    return False


def _increment_outreach_lead_metric(lead_id: int, field: str, value: int) -> None:
    try:
        row = (
            supabase.table("outreach_leads")
            .select(field)
            .eq("id", lead_id)
            .limit(1)
            .execute()
            .data
        )

        current = 0
        if row:
            current = int(row[0].get(field) or 0)

        supabase.table("outreach_leads").update({
            field: current + value,
            "last_updated": _utc_now().isoformat(),
        }).eq("id", lead_id).execute()

    except Exception as e:
        print(f"⚠ outreach_leads update failed ({field}): {e}")


def _upsert_crm_metric(lead_id: int, field: str, value: int) -> None:
    try:
        existing = (
            supabase.table("crm_analytics")
            .select("*")
            .eq("lead_id", lead_id)
            .limit(1)
            .execute()
        )

        now = _utc_now().isoformat()

        if existing.data:
            row = existing.data[0]
            payload = {"last_activity": now}

            payload[field] = int(row.get(field) or 0) + value

            supabase.table("crm_analytics").update(payload).eq("lead_id", lead_id).execute()
        else:
            payload = {
                "lead_id": lead_id,
                "engagement_score": 0,
                "emails_sent": 0,
                "opens": 0,
                "clicks": 0,
                "replies": 0,
                "conversions": 0,
                "last_activity": now,
            }
            payload[field] = value
            supabase.table("crm_analytics").insert(payload).execute()

    except Exception as e:
        print(f"⚠ crm_analytics update failed ({field}): {e}")


def _safe_redirect_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None

    url = url.strip()
    lowered = url.lower()

    if lowered.startswith((
        "http://localhost",
        "https://localhost",
        "http://127.0.0.1",
        "https://127.0.0.1",
    )):
        return None

    return url


async def _track_open(lead_id: int, campaign_id: Optional[int], metadata: Dict[str, Any]):
    try:
        if _recent_event_exists(lead_id, OPEN_CACHE, OPEN_DEDUP_SECONDS):
            return

        if not _is_browser_like(metadata.get("user_agent")):
            return

        now = _utc_now().isoformat()

        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="opened",
            metadata={**metadata, "ts": now},
        )

        _increment_outreach_lead_metric(lead_id, "open_count", 1)

        supabase.table("outreach_leads").update({
            "email_opened": True,
            "email_opened_at": now,
            "last_updated": now,
        }).eq("id", lead_id).execute()

        _upsert_crm_metric(lead_id, "opens", 1)

        print(f"📬 OPEN TRACKED → Lead {lead_id}")

    except Exception as e:
        print(f"❌ Open tracking failed: {e}")


async def _track_click(lead_id: int, campaign_id: Optional[int], metadata: Dict[str, Any], url: Optional[str]):
    try:
        if _recent_event_exists(lead_id, CLICK_CACHE, CLICK_DEDUP_SECONDS):
            return

        if not _is_browser_like(metadata.get("user_agent")):
            return

        now = _utc_now().isoformat()
        safe_url = _safe_redirect_url(url)

        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="clicked",
            metadata={**metadata, "ts": now, "url": safe_url},
        )

        _increment_outreach_lead_metric(lead_id, "click_count", 1)
        _upsert_crm_metric(lead_id, "clicks", 1)

        supabase.table("outreach_leads").update({
            "link_clicked": True,
            "link_clicked_at": now,
            "last_updated": now,
        }).eq("id", lead_id).execute()

        print(f"🔗 CLICK TRACKED → Lead {lead_id}")

    except Exception as e:
        print(f"❌ Click tracking failed: {e}")


@app.get("/")
def root():
    return {"status": "ok", "service": "pixel tracker running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/open/{lead_id}")
async def open_pixel(lead_id: int, request: Request, campaign_id: Optional[int] = Query(None)):
    async with PROCESS_LOCK:
        metadata = _safe_headers(request)
        resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)
        await _track_open(lead_id, resolved_campaign_id, metadata)
        return _pixel_response()


@app.get("/click/{lead_id}")
async def click(lead_id: int, request: Request, url: Optional[str] = Query(None), campaign_id: Optional[int] = Query(None)):
    async with PROCESS_LOCK:
        metadata = _safe_headers(request)
        resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)
        await _track_click(lead_id, resolved_campaign_id, metadata, url)

        safe_url = _safe_redirect_url(url)
        if safe_url:
            return RedirectResponse(url=safe_url)

        return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "outreach_engine.tracking.pixel_server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )