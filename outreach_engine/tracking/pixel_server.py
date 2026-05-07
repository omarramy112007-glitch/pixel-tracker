from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.pixel_tracker import (
    handle_pixel_click,
    handle_pixel_open,
)

print("🔥 PIXEL SERVER LOADED")

app = FastAPI(title="Outreach Engine Pixel Tracker")

print("🔄 Importing Gmail router...")
from outreach_engine.tracking.gmail_webhook import router as gmail_router
print("✅ Gmail router imported")

app.include_router(
    gmail_router,
    prefix="/gmail",
)

print("✅ Gmail router mounted successfully")

print("📦 REGISTERED ROUTES:")
for route in app.routes:
    try:
        print(f"{route.path} [{','.join(route.methods)}]")
    except Exception:
        print(route.path)

PROCESS_LOCK = asyncio.Lock()

OPEN_CACHE: Dict[str, float] = {}
CLICK_CACHE: Dict[str, float] = {}

OPEN_DEDUP_SECONDS = 900
CLICK_DEDUP_SECONDS = 300

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


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "pixel tracker running",
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }


@app.get("/gmail-test")
def gmail_test():
    return {
        "status": "gmail routes mounted"
    }


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
            cid = res.data[0].get("campaign_id")
            if cid is not None:
                return int(cid)

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
        return {
            "ip": None,
            "user_agent": None,
            "referer": None,
        }

    return {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "referer": request.headers.get("referer"),
    }


def _day_bucket() -> str:
    return _utc_now().date().isoformat()


def _cleanup_cache(cache: Dict[str, float], ttl_seconds: int) -> None:
    now_ts = _utc_now().timestamp()

    expired = [
        key
        for key, ts in cache.items()
        if (now_ts - ts) > ttl_seconds
    ]

    for key in expired:
        cache.pop(key, None)


def _remember(cache: Dict[str, float], key: str, ttl_seconds: int) -> bool:
    now_ts = _utc_now().timestamp()

    _cleanup_cache(cache, ttl_seconds)

    last_seen = cache.get(key)

    if last_seen is not None and (now_ts - last_seen) < ttl_seconds:
        return False

    cache[key] = now_ts
    return True


def _make_open_fingerprint(
    lead_id: int,
    campaign_id: Optional[int],
    metadata: Dict[str, Any],
) -> str:
    ua = (metadata.get("user_agent") or "").lower().strip()
    day = _day_bucket()
    cid = str(campaign_id) if campaign_id is not None else "none"
    raw = f"open:{lead_id}:{cid}:{day}:{ua}"

    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _make_click_fingerprint(
    lead_id: int,
    url: str,
    metadata: Dict[str, Any],
) -> str:
    ua = (metadata.get("user_agent") or "").lower().strip()
    day = _day_bucket()
    clean_url = _safe_redirect_url(url) or url.strip()
    raw = f"click:{lead_id}:{clean_url}:{day}:{ua}"

    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _safe_redirect_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None

    url = url.strip()
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        return None

    if parsed.hostname in {"localhost", "127.0.0.1"}:
        return None

    return urlunparse(parsed)


async def _call_tracker(handler, **kwargs):
    result = handler(**kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result


async def _handle_open(
    lead_id: int,
    request: Request,
    campaign_id: Optional[int] = None,
):
    metadata = _safe_headers(request)

    resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)

    if resolved_campaign_id is None:
        return _pixel_response()

    fingerprint = _make_open_fingerprint(
        lead_id,
        resolved_campaign_id,
        metadata,
    )

    async with PROCESS_LOCK:
        if not _remember(OPEN_CACHE, fingerprint, OPEN_DEDUP_SECONDS):
            return _pixel_response()

    try:
        await _call_tracker(
            handle_pixel_open,
            lead_id=lead_id,
            campaign_id=resolved_campaign_id,
            metadata=metadata,
        )
    except Exception as e:
        print(f"❌ open tracking error: {e}")

    return _pixel_response()


@app.get("/open/{lead_id}")
async def open_pixel(
    lead_id: int,
    request: Request,
    campaign_id: Optional[int] = Query(None),
):
    return await _handle_open(
        lead_id,
        request,
        campaign_id,
    )


@app.get("/track/open")
async def open_pixel_legacy(
    lead_id: int = Query(..., ge=1),
    request: Request = None,
    campaign_id: Optional[int] = Query(None),
):
    return await _handle_open(
        lead_id,
        request,
        campaign_id,
    )


async def _handle_click(
    lead_id: int,
    request: Request,
    redirect: Optional[str] = None,
    url: Optional[str] = None,
    campaign_id: Optional[int] = None,
):
    metadata = _safe_headers(request)
    safe_url = _safe_redirect_url(redirect or url)
    resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)

    if safe_url:
        fingerprint = _make_click_fingerprint(
            lead_id,
            safe_url,
            metadata,
        )

        async with PROCESS_LOCK:
            if not _remember(CLICK_CACHE, fingerprint, CLICK_DEDUP_SECONDS):
                return RedirectResponse(url=safe_url)

    if resolved_campaign_id is not None:
        try:
            await _call_tracker(
                handle_pixel_click,
                lead_id=lead_id,
                campaign_id=resolved_campaign_id,
                metadata={
                    **metadata,
                    "redirect": safe_url,
                },
            )
        except Exception as e:
            print(f"❌ click tracking error: {e}")

    if safe_url:
        return RedirectResponse(url=safe_url)

    return JSONResponse({"status": "ok"})


@app.get("/click/{lead_id}")
async def click(
    lead_id: int,
    request: Request,
    redirect: Optional[str] = Query(None),
    url: Optional[str] = Query(None),
    campaign_id: Optional[int] = Query(None),
):
    return await _handle_click(
        lead_id,
        request,
        redirect,
        url,
        campaign_id,
    )


@app.get("/track/click")
async def click_legacy(
    lead_id: int = Query(..., ge=1),
    request: Request = None,
    redirect: Optional[str] = Query(None),
    url: Optional[str] = Query(None),
    campaign_id: Optional[int] = Query(None),
):
    return await _handle_click(
        lead_id,
        request,
        redirect,
        url,
        campaign_id,
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))

    uvicorn.run(
        "outreach_engine.tracking.pixel_server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )