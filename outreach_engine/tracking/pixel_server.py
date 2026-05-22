from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response

from outreach_engine.database.supabase_client import supabase

DEBUG_LOGS = os.getenv("PIXEL_DEBUG_LOGS", "false").strip().lower() == "true"


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

# ── Gmail webhook router ─────────────────────────────────────────────────────
try:
    log("🔄 Importing Gmail router...", force=True)
    from outreach_engine.tracking.gmail_webhook import (
        router as gmail_router,
        process_gmail_webhook,
    )
    app.include_router(gmail_router, prefix="/gmail")
    log("✅ Gmail router mounted at /gmail", force=True)
except Exception as e:
    gmail_router = None
    process_gmail_webhook = None
    log(f"⚠ Gmail router disabled: {e}", force=True)


# ── Startup: begin reply polling in background ───────────────────────────────
GMAIL_WATCH_MODE          = os.getenv("GMAIL_WATCH_MODE", "poll").strip().lower()
GMAIL_POLL_INTERVAL       = int(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "60"))


@app.on_event("startup")
async def on_startup() -> None:
    if GMAIL_WATCH_MODE == "watch":
        try:
            from outreach_engine.tracking.gmail_watcher import start_watch
            result = start_watch()
            log(f"✅ Gmail watch renewed on startup: {result}", force=True)
        except Exception as e:
            log(f"⚠ Gmail watch renewal failed: {e} — falling back to poll", force=True)
            _start_poll_task()
    else:
        _start_poll_task()


def _start_poll_task() -> None:
    try:
        from outreach_engine.tracking.gmail_watcher import start_reply_polling
        log(f"👂 Starting reply polling every {GMAIL_POLL_INTERVAL}s", force=True)
        asyncio.create_task(start_reply_polling(GMAIL_POLL_INTERVAL))
    except Exception as e:
        log(f"⚠ Could not start reply polling: {e}", force=True)


# ── Reply monitor endpoints ───────────────────────────────────────────────────
@app.get("/replies/check")
async def check_replies_get():
    """Manually trigger a reply check."""
    try:
        from outreach_engine.tracking.gmail_watcher import check_for_replies
        processed = check_for_replies()
        return {"status": "ok", "processed": len(processed), "replies": processed}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/replies/check")
async def check_replies_post():
    """Manually trigger a reply check (POST)."""
    try:
        from outreach_engine.tracking.gmail_watcher import check_for_replies
        processed = check_for_replies()
        return {"status": "ok", "processed": len(processed), "replies": processed}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/replies/renew-watch")
async def renew_watch():
    """Renew Gmail watch subscription — call every 6 days via cron."""
    try:
        from outreach_engine.tracking.gmail_watcher import start_watch
        result = start_watch()
        return {"status": "ok", "watch": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Debug ─────────────────────────────────────────────────────────────────────
log("📦 REGISTERED ROUTES:", force=True)
for route in app.routes:
    path    = getattr(route, "path", None)
    methods = getattr(route, "methods", None)
    if path:
        log(f"  {path} {list(methods or [])}", force=True)

PROCESS_LOCK = asyncio.Lock()

OPEN_CACHE:  Dict[str, float] = {}
CLICK_CACHE: Dict[str, float] = {}

OPEN_DEDUP_SECONDS  = 900
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
    return {"status": "ok", "service": "pixel tracker running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/debug/routes")
async def debug_routes():
    return {
        "routes": [
            {
                "path":    getattr(route, "path", None),
                "methods": list(getattr(route, "methods", []) or []),
            }
            for route in app.routes
        ]
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _pixel_response() -> Response:
    return Response(
        content=PIXEL,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma":        "no-cache",
            "Expires":       "0",
        },
    )


def _safe_headers(request: Optional[Request]) -> Dict[str, Any]:
    if not request:
        return {"ip": None, "user_agent": None, "referer": None}
    return {
        "ip":         request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "referer":    request.headers.get("referer"),
    }


def _cleanup_cache(cache: Dict[str, float], ttl_seconds: int) -> None:
    now_ts  = _utc_now().timestamp()
    expired = [key for key, ts in cache.items() if (now_ts - ts) > ttl_seconds]
    for key in expired:
        cache.pop(key, None)


def _remember(cache: Dict[str, float], key: str, ttl_seconds: int) -> bool:
    now_ts   = _utc_now().timestamp()
    _cleanup_cache(cache, ttl_seconds)
    last_seen = cache.get(key)
    if last_seen is not None and (now_ts - last_seen) < ttl_seconds:
        return False
    cache[key] = now_ts
    return True


def _day_bucket() -> str:
    return _utc_now().date().isoformat()


def _make_open_fingerprint(
    lead_id: int, campaign_id: Optional[int], metadata: Dict[str, Any]
) -> str:
    ua  = (metadata.get("user_agent") or "").lower().strip()
    day = _day_bucket()
    cid = str(campaign_id) if campaign_id is not None else "none"
    raw = f"open:{lead_id}:{cid}:{day}:{ua}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _make_click_fingerprint(
    lead_id: int, url: str, metadata: Dict[str, Any]
) -> str:
    ua        = (metadata.get("user_agent") or "").lower().strip()
    day       = _day_bucket()
    clean_url = _safe_redirect_url(url) or url.strip()
    raw       = f"click:{lead_id}:{clean_url}:{day}:{ua}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _safe_redirect_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url    = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.hostname in {"localhost", "127.0.0.1"}:
        return None
    return urlunparse(parsed)


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
        log(f"⚠ campaign resolve error: {e}", force=True)
    return None


def _resolve_system_lead_id_from_email(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    try:
        res = (
            supabase.table("leads")
            .select("id")
            .ilike("email", email)
            .limit(1)
            .execute()
        )
        if res.data:
            lead_id = res.data[0].get("id")
            return str(lead_id) if lead_id else None
    except Exception as e:
        log(f"⚠ system lead resolve error: {e}", force=True)
    return None


def _record_lead_event(
    system_lead_id: Optional[str],
    campaign_id: int,
    event_type: str,
    metadata: Dict[str, Any],
) -> None:
    if not system_lead_id:
        return
    try:
        supabase.table("lead_events").insert({
            "lead_id":    system_lead_id,
            "campaign_id": campaign_id,
            "event_type": event_type,
            "metadata":   metadata,
            "timestamp":  _utc_now().isoformat(),
        }).execute()
    except Exception as e:
        log(f"⚠ lead_events insert failed: {e}", force=True)


def _update_crm_analytics(
    system_lead_id: Optional[str], field: str, increment: int = 1
) -> None:
    if not system_lead_id:
        return

    now = _utc_now().isoformat()

    try:
        existing = (
            supabase.table("crm_analytics")
            .select("engagement_score, emails_sent, opens, clicks, replies, conversions")
            .eq("lead_id", system_lead_id)
            .limit(1)
            .execute()
        )

        if existing.data:
            row     = existing.data[0]
            payload: Dict[str, Any] = {
                "last_activity":    now,
                "engagement_score": float(row.get("engagement_score") or 0),
                "emails_sent":      int(row.get("emails_sent") or 0),
                "opens":            int(row.get("opens") or 0),
                "clicks":           int(row.get("clicks") or 0),
                "replies":          int(row.get("replies") or 0),
                "conversions":      int(row.get("conversions") or 0),
            }
            if field == "opens":
                payload["opens"] += increment
            elif field == "clicks":
                payload["clicks"] += increment
            elif field == "replies":
                payload["replies"]          += increment
                payload["engagement_score"] += 5
            elif field == "conversions":
                payload["conversions"] += increment

            supabase.table("crm_analytics").update(payload).eq("lead_id", system_lead_id).execute()
        else:
            supabase.table("crm_analytics").insert({
                "lead_id":          system_lead_id,
                "engagement_score": 5 if field == "replies" else 0,
                "emails_sent":      0,
                "opens":            1 if field == "opens"       else 0,
                "clicks":           1 if field == "clicks"      else 0,
                "replies":          1 if field == "replies"     else 0,
                "conversions":      1 if field == "conversions" else 0,
                "last_activity":    now,
            }).execute()

    except Exception as e:
        log(f"⚠ crm_analytics sync failed: {e}", force=True)


def _update_outreach_leads(lead_id: int, event_type: str) -> None:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("open_count, click_count, email_opened, link_clicked")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        row     = (res.data or [{}])[0]
        now     = _utc_now().isoformat()
        updates: Dict[str, Any] = {"last_updated": now}

        if event_type == "opened":
            updates["open_count"]   = int(row.get("open_count") or 0) + 1
            updates["email_opened"] = True
            if not row.get("email_opened"):
                updates["email_opened_at"] = now
        elif event_type == "clicked":
            updates["click_count"]  = int(row.get("click_count") or 0) + 1
            updates["link_clicked"] = True

        supabase.table("outreach_leads").update(updates).eq("id", lead_id).execute()

    except Exception as e:
        log(f"⚠ outreach_leads sync failed: {e}", force=True)


async def _track_open_db(lead_id: int, campaign_id: int, metadata: Dict[str, Any]) -> None:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("open_count, email")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        current_count = 0
        email         = None
        if res.data:
            row           = res.data[0]
            current_count = int(row.get("open_count") or 0)
            email         = (row.get("email") or "").strip().lower() or None

        _update_outreach_leads(lead_id, "opened")

        system_lead_id = _resolve_system_lead_id_from_email(email)
        event_metadata = {
            **metadata,
            "ts":               _utc_now().isoformat(),
            "channel":          "email",
            "source":           "pixel",
            "campaign_id":      campaign_id,
            "outreach_lead_id": lead_id,
        }

        _record_lead_event(system_lead_id, campaign_id, "opened", event_metadata)
        _update_crm_analytics(system_lead_id, "opens", 1)

        log(f"📬 OPEN TRACKED → Lead {lead_id} | count={current_count + 1}", force=True)

    except Exception as e:
        log(f"❌ open tracking db error: {e}", force=True)


async def _track_click_db(lead_id: int, campaign_id: int, metadata: Dict[str, Any]) -> None:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("click_count, email")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        current_count = 0
        email         = None
        if res.data:
            row           = res.data[0]
            current_count = int(row.get("click_count") or 0)
            email         = (row.get("email") or "").strip().lower() or None

        _update_outreach_leads(lead_id, "clicked")

        system_lead_id = _resolve_system_lead_id_from_email(email)
        event_metadata = {
            **metadata,
            "ts":               _utc_now().isoformat(),
            "channel":          "email",
            "source":           "pixel",
            "campaign_id":      campaign_id,
            "outreach_lead_id": lead_id,
        }

        _record_lead_event(system_lead_id, campaign_id, "clicked", event_metadata)
        _update_crm_analytics(system_lead_id, "clicks", 1)

        log(f"🖱 CLICK TRACKED → Lead {lead_id} | count={current_count + 1}", force=True)

    except Exception as e:
        log(f"❌ click tracking db error: {e}", force=True)


async def _handle_open(
    lead_id: int, request: Request, campaign_id: Optional[int] = None
):
    metadata             = _safe_headers(request)
    resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)

    if resolved_campaign_id is None:
        return _pixel_response()

    fingerprint = _make_open_fingerprint(lead_id, resolved_campaign_id, metadata)

    async with PROCESS_LOCK:
        if not _remember(OPEN_CACHE, fingerprint, OPEN_DEDUP_SECONDS):
            return _pixel_response()

    try:
        await _track_open_db(lead_id, resolved_campaign_id, metadata)
    except Exception as e:
        log(f"❌ open tracking error: {e}", force=True)

    return _pixel_response()


@app.get("/open/{lead_id}")
async def open_pixel(
    lead_id:     int,
    request:     Request,
    campaign_id: Optional[int] = Query(None),
):
    return await _handle_open(lead_id, request, campaign_id)


@app.get("/track/open")
async def open_pixel_legacy(
    lead_id:     int             = Query(..., ge=1),
    request:     Request         = None,
    campaign_id: Optional[int]   = Query(None),
):
    return await _handle_open(lead_id, request, campaign_id)


async def _handle_click(
    lead_id:     int,
    request:     Request,
    redirect:    Optional[str] = None,
    url:         Optional[str] = None,
    campaign_id: Optional[int] = None,
):
    metadata             = _safe_headers(request)
    safe_url             = _safe_redirect_url(redirect or url)
    resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)

    if safe_url:
        fingerprint = _make_click_fingerprint(lead_id, safe_url, metadata)
        async with PROCESS_LOCK:
            if not _remember(CLICK_CACHE, fingerprint, CLICK_DEDUP_SECONDS):
                return RedirectResponse(url=safe_url)

    if resolved_campaign_id is not None:
        try:
            await _track_click_db(
                lead_id,
                resolved_campaign_id,
                {**metadata, "redirect": safe_url},
            )
        except Exception as e:
            log(f"❌ click tracking error: {e}", force=True)

    if safe_url:
        return RedirectResponse(url=safe_url)

    return JSONResponse({"status": "ok"})


@app.get("/click/{lead_id}")
async def click(
    lead_id:     int,
    request:     Request,
    redirect:    Optional[str] = Query(None),
    url:         Optional[str] = Query(None),
    campaign_id: Optional[int] = Query(None),
):
    return await _handle_click(lead_id, request, redirect, url, campaign_id)


@app.get("/track/click")
async def click_legacy(
    lead_id:     int           = Query(..., ge=1),
    request:     Request       = None,
    redirect:    Optional[str] = Query(None),
    url:         Optional[str] = Query(None),
    campaign_id: Optional[int] = Query(None),
):
    return await _handle_click(lead_id, request, redirect, url, campaign_id)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "outreach_engine.tracking.pixel_server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
