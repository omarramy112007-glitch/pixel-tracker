from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response

from outreach_engine.database.supabase_client import supabase

DEBUG_LOGS = os.getenv("PIXEL_DEBUG_LOGS", "true").strip().lower() == "true"


def log(*args, force: bool = False) -> None:
    if force or DEBUG_LOGS:
        print(*args)


log("🔥 PIXEL SERVER LOADED", force=True)

app = FastAPI(title="Outreach Engine Pixel Tracker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────
# Gmail reply tracking
# ─────────────────────────────────────────────────────────────

check_for_replies = None
start_reply_polling = None
start_watch = None

try:
    log("🔄 Importing Gmail watcher...", force=True)

    from outreach_engine.tracking.gmail_watcher import (
        check_for_replies,
        start_reply_polling,
        start_watch,
    )

    log("✅ Gmail watcher imported", force=True)

except Exception as e:
    log(f"⚠ Gmail watcher disabled: {e}", force=True)

# Gmail webhook router
try:
    from outreach_engine.tracking.gmail_webhook import (
        router as gmail_router,
    )

    app.include_router(gmail_router, prefix="/gmail")

    log("✅ Gmail webhook router mounted", force=True)

except Exception as e:
    log(f"⚠ Gmail webhook router disabled: {e}", force=True)

GMAIL_WATCH_MODE = (
    os.getenv("GMAIL_WATCH_MODE", "poll")
    .strip()
    .lower()
)

GMAIL_POLL_INTERVAL = int(
    os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "60")
)


@app.on_event("startup")
async def on_startup() -> None:
    if GMAIL_WATCH_MODE == "watch" and start_watch:
        try:
            result = start_watch()

            log(
                f"✅ Gmail watch renewed: {result}",
                force=True,
            )

            return

        except Exception as e:
            log(
                f"⚠ Gmail watch failed: {e}",
                force=True,
            )

    _start_poll_task()


def _start_poll_task() -> None:
    if not start_reply_polling:
        log("⚠ Gmail polling unavailable", force=True)
        return

    try:
        log(
            f"👂 Starting reply polling every "
            f"{GMAIL_POLL_INTERVAL}s",
            force=True,
        )

        asyncio.create_task(
            start_reply_polling(
                GMAIL_POLL_INTERVAL
            )
        )

    except Exception as e:
        log(
            f"⚠ Could not start reply polling: {e}",
            force=True,
        )


# ─────────────────────────────────────────────────────────────
# Reply endpoints
# ─────────────────────────────────────────────────────────────

@app.get("/replies/check")
async def check_replies_get():
    if not check_for_replies:
        return {
            "status": "error",
            "error": "gmail watcher unavailable",
        }

    try:
        processed = check_for_replies()

        return {
            "status": "ok",
            "processed": len(processed),
            "replies": processed,
        }

    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }


@app.post("/replies/check")
async def check_replies_post():
    return await check_replies_get()


@app.post("/replies/renew-watch")
async def renew_watch():
    if not start_watch:
        return {
            "status": "error",
            "error": "gmail watch unavailable",
        }

    try:
        result = start_watch()

        return {
            "status": "ok",
            "watch": result,
        }

    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────
# Tracking internals
# ─────────────────────────────────────────────────────────────

PROCESS_LOCK = asyncio.Lock()

OPEN_CACHE: Dict[str, float] = {}
CLICK_CACHE: Dict[str, float] = {}

# IMPORTANT:
# lower dedup for debugging
OPEN_DEDUP_SECONDS = 5
CLICK_DEDUP_SECONDS = 5

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
    return {"status": "ok"}


@app.get("/debug/routes")
async def debug_routes():
    return {
        "routes": [
            {
                "path": getattr(r, "path", None),
                "methods": list(
                    getattr(r, "methods", []) or []
                ),
            }
            for r in app.routes
        ]
    }


@app.get("/debug/lead/{lead_id}")
async def debug_lead(lead_id: int):
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        return {
            "status": "ok",
            "data": res.data,
        }

    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _pixel_response() -> Response:
    return Response(
        content=PIXEL,
        media_type="image/gif",
        headers={
            "Cache-Control":
                "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _safe_headers(
    request: Optional[Request],
) -> Dict[str, Any]:
    if not request:
        return {
            "ip": None,
            "user_agent": None,
            "referer": None,
        }

    return {
        "ip":
            request.client.host
            if request.client
            else None,
        "user_agent":
            request.headers.get("user-agent"),
        "referer":
            request.headers.get("referer"),
    }


def _cleanup_cache(
    cache: Dict[str, float],
    ttl_seconds: int,
) -> None:
    now_ts = _utc_now().timestamp()

    expired = [
        key
        for key, ts in cache.items()
        if (now_ts - ts) > ttl_seconds
    ]

    for key in expired:
        cache.pop(key, None)


def _remember(
    cache: Dict[str, float],
    key: str,
    ttl_seconds: int,
) -> bool:
    now_ts = _utc_now().timestamp()

    _cleanup_cache(cache, ttl_seconds)

    last_seen = cache.get(key)

    if (
        last_seen is not None
        and (now_ts - last_seen) < ttl_seconds
    ):
        return False

    cache[key] = now_ts

    return True


def _safe_redirect_url(
    url: Optional[str],
) -> Optional[str]:
    if not url:
        return None

    url = url.strip()

    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        return None

    if parsed.hostname in {
        "localhost",
        "127.0.0.1",
    }:
        return None

    return urlunparse(parsed)


def _resolve_campaign_id(
    lead_id: int,
) -> Optional[int]:
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
        log(
            f"⚠ campaign resolve error: {e}",
            force=True,
        )

    return None


# ─────────────────────────────────────────────────────────────
# FIXED OPEN TRACKING
# ─────────────────────────────────────────────────────────────

async def _track_open_db(
    lead_id: int,
    campaign_id: int,
    metadata: Dict[str, Any],
) -> None:
    try:
        log(
            f"📥 TRACK OPEN START lead={lead_id}",
            force=True,
        )

        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        if not res.data:
            log(
                f"❌ Lead not found: {lead_id}",
                force=True,
            )
            return

        row = res.data[0]

        current_count = int(
            row.get("open_count") or 0
        )

        now = _utc_now().isoformat()

        updates = {
            "open_count": current_count + 1,
            "email_opened": True,
            "last_updated": now,
        }

        # only set first open timestamp once
        if not row.get("email_opened_at"):
            updates["email_opened_at"] = now

        log(
            f"📝 Updating lead {lead_id}: {updates}",
            force=True,
        )

        update_res = (
            supabase.table("outreach_leads")
            .update(updates)
            .eq("id", lead_id)
            .execute()
        )

        log(
            f"✅ DB UPDATE RESULT: {update_res}",
            force=True,
        )

        log(
            f"📬 OPEN TRACKED → Lead {lead_id} "
            f"| count={current_count + 1}",
            force=True,
        )

    except Exception as e:
        log(
            f"❌ OPEN TRACKING FAILED: {e}",
            force=True,
        )


async def _track_click_db(
    lead_id: int,
    campaign_id: int,
    metadata: Dict[str, Any],
) -> None:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        if not res.data:
            return

        row = res.data[0]

        current_count = int(
            row.get("click_count") or 0
        )

        now = _utc_now().isoformat()

        updates = {
            "click_count": current_count + 1,
            "last_updated": now,
        }

        # ONLY update if column exists
        if "link_clicked" in row:
            updates["link_clicked"] = True

        (
            supabase.table("outreach_leads")
            .update(updates)
            .eq("id", lead_id)
            .execute()
        )

        log(
            f"🖱 CLICK TRACKED → Lead {lead_id} "
            f"| count={current_count + 1}",
            force=True,
        )

    except Exception as e:
        log(
            f"❌ click tracking db error: {e}",
            force=True,
        )


# ─────────────────────────────────────────────────────────────
# OPEN ROUTES
# ─────────────────────────────────────────────────────────────

async def _handle_open(
    lead_id: int,
    request: Request,
    campaign_id: Optional[int] = None,
):
    log(
        f"🔥 OPEN REQUEST RECEIVED lead={lead_id}",
        force=True,
    )

    metadata = _safe_headers(request)

    resolved_campaign_id = (
        campaign_id
        or _resolve_campaign_id(lead_id)
    )

    log(
        f"📌 Campaign ID: {resolved_campaign_id}",
        force=True,
    )

    # IMPORTANT FIX:
    # allow tracking even without campaign id
    if resolved_campaign_id is None:
        resolved_campaign_id = 0

    fingerprint = hashlib.sha1(
        f"{lead_id}:{metadata}".encode()
    ).hexdigest()

    async with PROCESS_LOCK:
        if not _remember(
            OPEN_CACHE,
            fingerprint,
            OPEN_DEDUP_SECONDS,
        ):
            log(
                "⚠ OPEN DEDUP BLOCKED",
                force=True,
            )
            return _pixel_response()

    try:
        await _track_open_db(
            lead_id,
            resolved_campaign_id,
            metadata,
        )

    except Exception as e:
        log(
            f"❌ open tracking error: {e}",
            force=True,
        )

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


# ─────────────────────────────────────────────────────────────
# CLICK ROUTES
# ─────────────────────────────────────────────────────────────

async def _handle_click(
    lead_id: int,
    request: Request,
    redirect: Optional[str] = None,
    url: Optional[str] = None,
    campaign_id: Optional[int] = None,
):
    metadata = _safe_headers(request)

    safe_url = _safe_redirect_url(
        redirect or url
    )

    resolved_campaign_id = (
        campaign_id
        or _resolve_campaign_id(lead_id)
        or 0
    )

    if safe_url:
        try:
            await _track_click_db(
                lead_id,
                resolved_campaign_id,
                {
                    **metadata,
                    "redirect": safe_url,
                },
            )

        except Exception as e:
            log(
                f"❌ click tracking error: {e}",
                force=True,
            )

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

    port = int(
        os.environ.get("PORT", 8000)
    )

    uvicorn.run(
        "outreach_engine.tracking.pixel_server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
