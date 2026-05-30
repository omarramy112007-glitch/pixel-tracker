# outreach_engine/tracking/pixel_server.py

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

DEBUG_LOGS = os.getenv("PIXEL_DEBUG_LOGS", "false").strip().lower() == "true"

# ── Dedup windows + send guard from environment ──────────────────────────────
OPEN_DEDUP_SECONDS       = int(os.getenv("OPEN_DEDUP_SECONDS", "900"))
CLICK_DEDUP_SECONDS      = int(os.getenv("CLICK_DEDUP_SECONDS", "300"))
MIN_SEND_TO_OPEN_SECONDS = int(os.getenv("MIN_SEND_TO_OPEN_SECONDS", "2"))

# ── Sender IP blocklist from environment ─────────────────────────────────────
_RAW_BLOCKED_IPS = os.getenv("PIXEL_BLOCKED_IPS", "").strip()
BLOCKED_IPS: set[str] = (
    {ip.strip() for ip in _RAW_BLOCKED_IPS.split(",") if ip.strip()}
    if _RAW_BLOCKED_IPS
    else set()
)

# ── Bot UA patterns ──────────────────────────────────────────────────────────
# IMPORTANT: We do NOT block "googleimageproxy" — Gmail routes REAL opens
# through that proxy. Blocking it kills real Gmail opens. The 2-second time
# guard is what separates the prefetch-on-send from a real human open.
# We also do NOT block email clients (outlook / apple mail) for the same reason.
BOT_UA_PATTERNS = [
    # Google crawlers (NOT the image proxy)
    "googlebot",
    "google-apps-script",
    "google-read-aloud",
    "apis-google",
    "feedfetcher-google",
    # Microsoft crawlers
    "msnbot",
    "bingbot",
    # Security / spam scanners
    "barracudacentral",
    "proofpoint",
    "mimecast",
    "symantec",
    "sophos",
    "trend micro",
    "cloudmark",
    "spamhaus",
    "postfix",
    # Generic crawlers / scripts
    "wget",
    "curl",
    "python-requests",
    "python-httpx",
    "libwww",
    "jakarta",
    "apache-httpclient",
    "java/",
    "go-http-client",
    "ruby",
    "scrapy",
    "phantomjs",
    "headlesschrome",
    "prerender",
]


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

# ── Gmail reply tracking ──────────────────────────────────────────────────────
check_for_replies   = None
start_reply_polling = None
start_watch         = None

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

try:
    from outreach_engine.tracking.gmail_webhook import router as gmail_router
    app.include_router(gmail_router, prefix="/gmail")
    log("✅ Gmail webhook router mounted at /gmail", force=True)
except Exception as e:
    log(f"⚠ Gmail webhook router disabled: {e}", force=True)

GMAIL_WATCH_MODE    = os.getenv("GMAIL_WATCH_MODE", "poll").strip().lower()
GMAIL_POLL_INTERVAL = int(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "60"))


@app.on_event("startup")
async def on_startup() -> None:
    if GMAIL_WATCH_MODE == "watch" and start_watch:
        try:
            result = start_watch()
            log(f"✅ Gmail watch renewed on startup: {result}", force=True)
            return
        except Exception as e:
            log(f"⚠ Gmail watch renewal failed: {e} — falling back to poll", force=True)
    _start_poll_task()


def _start_poll_task() -> None:
    if not start_reply_polling:
        log("⚠ Gmail polling unavailable", force=True)
        return
    try:
        log(f"👂 Starting reply polling every {GMAIL_POLL_INTERVAL}s", force=True)
        asyncio.create_task(start_reply_polling(GMAIL_POLL_INTERVAL))
    except Exception as e:
        log(f"⚠ Could not start reply polling: {e}", force=True)


# ── Reply endpoints ───────────────────────────────────────────────────────────
@app.get("/replies/check")
async def check_replies_get():
    if not check_for_replies:
        return {"status": "error", "error": "gmail watcher unavailable"}
    try:
        processed = check_for_replies()
        return {"status": "ok", "processed": len(processed), "replies": processed}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/replies/check")
async def check_replies_post():
    if not check_for_replies:
        return {"status": "error", "error": "gmail watcher unavailable"}
    try:
        processed = check_for_replies()
        return {"status": "ok", "processed": len(processed), "replies": processed}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/replies/renew-watch")
async def renew_watch():
    if not start_watch:
        return {"status": "error", "error": "gmail watch unavailable"}
    try:
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

# ── Tracking internals ────────────────────────────────────────────────────────
PROCESS_LOCK: asyncio.Lock     = asyncio.Lock()
OPEN_CACHE:   Dict[str, float] = {}
CLICK_CACHE:  Dict[str, float] = {}

PIXEL = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
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
                "path":    getattr(r, "path", None),
                "methods": list(getattr(r, "methods", []) or []),
            }
            for r in app.routes
        ]
    }


# ── Helpers ───────────────────────────────────────────────────────────────────
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
    expired = [k for k, ts in cache.items() if (now_ts - ts) > ttl_seconds]
    for k in expired:
        cache.pop(k, None)


def _remember(cache: Dict[str, float], key: str, ttl_seconds: int) -> bool:
    now_ts = _utc_now().timestamp()
    _cleanup_cache(cache, ttl_seconds)
    last_seen = cache.get(key)
    if last_seen is not None and (now_ts - last_seen) < ttl_seconds:
        return False
    cache[key] = now_ts
    return True


def _day_bucket() -> str:
    return _utc_now().date().isoformat()


def _make_open_fingerprint(
    lead_id: int,
    campaign_id: Optional[int],
    email_type: Optional[str] = None,
    last_email_sent: Optional[str] = None,
) -> str:
    """
    Dedup per individual email send.

    Including BOTH email_type AND last_email_sent means:
    - A cold open and a follow-up open never collide (different email_type)
    - Follow-up #1 and follow-up #2 never collide (different last_email_sent)
    Each new email send produces a brand-new fingerprint, so a single open
    of any email is always counted exactly once within the dedup window.

    Falls back to the day bucket if last_email_sent is missing.
    """
    cid  = str(campaign_id) if campaign_id is not None else "none"
    et   = (email_type or "none").strip().lower()
    sent = (str(last_email_sent).strip() if last_email_sent else _day_bucket())
    return hashlib.sha1(f"open:{lead_id}:{cid}:{et}:{sent}".encode()).hexdigest()


def _make_click_fingerprint(
    lead_id: int,
    url: str,
    metadata: Dict[str, Any],
) -> str:
    ua        = (metadata.get("user_agent") or "").lower().strip()
    day       = _day_bucket()
    clean_url = _safe_redirect_url(url) or url.strip()
    return hashlib.sha1(f"click:{lead_id}:{clean_url}:{day}:{ua}".encode()).hexdigest()


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


def _resolve_lead_meta(lead_id: int) -> Dict[str, Any]:
    """
    Fetch campaign_id + last_email_sent in a single query so the dedup
    fingerprint can be built accurately (per-send dedup).
    """
    try:
        res = (
            supabase.table("outreach_leads")
            .select("campaign_id, last_email_sent")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    except Exception as e:
        log(f"⚠ lead meta resolve error: {e}", force=True)
    return {}


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
    system_lead_id: Optional[str],
    field: str,
    increment: int = 1,
) -> None:
    if not system_lead_id:
        return
    now = _utc_now().isoformat()
    try:
        existing = (
            supabase.table("crm_analytics")
            .select("*")
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
                "engagement_score": 5 if field == "replies"     else 0,
                "emails_sent":      0,
                "opens":            1 if field == "opens"       else 0,
                "clicks":           1 if field == "clicks"      else 0,
                "replies":          1 if field == "replies"     else 0,
                "conversions":      1 if field == "conversions" else 0,
                "last_activity":    now,
            }).execute()
    except Exception as e:
        log(f"⚠ crm_analytics sync failed: {e}", force=True)


# ── Bot & sender filtering ────────────────────────────────────────────────────
def _is_bot_request(request: Optional[Request]) -> bool:
    """
    Block obvious bots/crawlers/scanners and your own sender IP.
    NOTE: GoogleImageProxy is intentionally NOT blocked here — Gmail serves
    real opens through it. The 2-second send guard handles the prefetch.
    Returns True if the request should be IGNORED.
    """
    if not request:
        return True

    # IP filter
    client_ip = request.client.host if request.client else None
    if client_ip and client_ip in BLOCKED_IPS:
        log(f"🚫 Open blocked — sender IP: {client_ip}", force=True)
        return True

    # User-Agent filter
    ua = (request.headers.get("user-agent") or "").lower().strip()
    if not ua:
        log("🚫 Open blocked — empty User-Agent", force=True)
        return True

    for pattern in BOT_UA_PATTERNS:
        if pattern in ua:
            log(f"🚫 Open blocked — bot UA matched '{pattern}': {ua[:120]}", force=True)
            return True

    return False


def _is_too_soon_after_send(last_email_sent: Optional[str], lead_id: int) -> bool:
    """
    Skip opens that fire within MIN_SEND_TO_OPEN_SECONDS of the send.
    This catches the instant prefetch-on-send, not a real open.
    If last_email_sent is missing, the open is allowed (returns False).
    """
    if not last_email_sent:
        return False
    try:
        sent_time = datetime.fromisoformat(str(last_email_sent).replace("Z", "+00:00"))
        if sent_time.tzinfo is None:
            sent_time = sent_time.replace(tzinfo=timezone.utc)
        elapsed = (_utc_now() - sent_time).total_seconds()
        if elapsed < MIN_SEND_TO_OPEN_SECONDS:
            log(
                f"⏳ Open ignored — too soon after send "
                f"({elapsed:.1f}s < {MIN_SEND_TO_OPEN_SECONDS}s) → lead_id={lead_id}",
                force=True,
            )
            return True
    except Exception as e:
        log(f"⚠ send-time parse error for lead {lead_id}: {e}", force=True)
    return False


# ── DB tracking ───────────────────────────────────────────────────────────────
async def _track_open_db(
    lead_id: int,
    campaign_id: int,
    metadata: Dict[str, Any],
    email_type: Optional[str] = None,
) -> None:
    try:
        res = (
            supabase.table("outreach_leads")
            .select(
                "id, email, campaign_id, open_count, followup_open_count, "
                "followup_status, email_opened, last_email_sent"
            )
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            log(f"⚠ Lead {lead_id} not found for open tracking", force=True)
            return

        row             = res.data[0]
        email           = (row.get("email") or "").strip().lower() or None
        followup_status = (row.get("followup_status") or "").strip().lower()

        # ── 2-second send guard ──────────────────────────────────────────────
        if _is_too_soon_after_send(row.get("last_email_sent"), lead_id):
            return
        # ─────────────────────────────────────────────────────────────────────

        now     = _utc_now().isoformat()
        updates: Dict[str, Any] = {
            "last_updated": now,
            "email_opened": True,
        }
        if not row.get("email_opened"):
            updates["email_opened_at"] = now

        # Route to correct counter
        if email_type == "followup":
            updates["followup_open_count"] = int(row.get("followup_open_count") or 0) + 1
            log(f"📬 followup_open_count++ → Lead {lead_id} (via email_type)", force=True)
        elif email_type == "cold":
            updates["open_count"] = int(row.get("open_count") or 0) + 1
            log(f"📬 open_count++ → Lead {lead_id} (via email_type)", force=True)
        else:
            if followup_status in {"no_open", "soft_open"}:
                updates["followup_open_count"] = int(row.get("followup_open_count") or 0) + 1
                log(f"📬 followup_open_count++ → Lead {lead_id} (fallback)", force=True)
            else:
                updates["open_count"] = int(row.get("open_count") or 0) + 1
                log(f"📬 open_count++ → Lead {lead_id} (fallback)", force=True)

        supabase.table("outreach_leads").update(updates).eq("id", lead_id).execute()

        system_lead_id = _resolve_system_lead_id_from_email(email)
        event_metadata = {
            **metadata,
            "ts":               _utc_now().isoformat(),
            "channel":          "email",
            "source":           "pixel",
            "campaign_id":      campaign_id,
            "outreach_lead_id": lead_id,
            "email_type":       email_type,
        }
        _record_lead_event(system_lead_id, campaign_id, "opened", event_metadata)
        _update_crm_analytics(system_lead_id, "opens", 1)

    except Exception as e:
        log(f"❌ open tracking db error: {e}", force=True)


async def _track_click_db(
    lead_id: int,
    campaign_id: int,
    metadata: Dict[str, Any],
) -> None:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("id, email, click_count")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            return

        row   = res.data[0]
        email = (row.get("email") or "").strip().lower() or None
        now   = _utc_now().isoformat()

        updates: Dict[str, Any] = {
            "click_count":  int(row.get("click_count") or 0) + 1,
            "link_clicked": True,
            "last_updated": now,
        }
        supabase.table("outreach_leads").update(updates).eq("id", lead_id).execute()

        system_lead_id = _resolve_system_lead_id_from_email(email)
        event_metadata = {
            **metadata,
            "ts":               now,
            "channel":          "email",
            "source":           "pixel",
            "campaign_id":      campaign_id,
            "outreach_lead_id": lead_id,
        }
        _record_lead_event(system_lead_id, campaign_id, "clicked", event_metadata)
        _update_crm_analytics(system_lead_id, "clicks", 1)

        log(f"🖱 CLICK TRACKED → Lead {lead_id}", force=True)
    except Exception as e:
        log(f"❌ click tracking db error: {e}", force=True)


# ── Route handlers ────────────────────────────────────────────────────────────
async def _handle_open(
    lead_id: int,
    request: Request,
    campaign_id: Optional[int] = None,
    email_type:  Optional[str] = None,
) -> Response:
    # Gate 1: Bot + IP filter (crawlers/scanners only — NOT GoogleImageProxy)
    if _is_bot_request(request):
        return _pixel_response()

    metadata = _safe_headers(request)

    # Resolve campaign_id + last_email_sent together (for accurate dedup)
    lead_meta            = _resolve_lead_meta(lead_id)
    resolved_campaign_id = campaign_id or (
        int(lead_meta["campaign_id"]) if lead_meta.get("campaign_id") is not None else None
    )
    last_email_sent      = lead_meta.get("last_email_sent")

    if resolved_campaign_id is None:
        log(f"⚠ No campaign_id for lead {lead_id} — open not tracked", force=True)
        return _pixel_response()

    # Gate 2: Per-send dedup (lead + campaign + email_type + last_email_sent)
    fingerprint = _make_open_fingerprint(
        lead_id,
        resolved_campaign_id,
        email_type,
        last_email_sent,
    )
    async with PROCESS_LOCK:
        if not _remember(OPEN_CACHE, fingerprint, OPEN_DEDUP_SECONDS):
            log(
                f"🧠 Duplicate open ignored → lead_id={lead_id} "
                f"(email_type={email_type})",
                force=True,
            )
            return _pixel_response()

    # Gate 3: 2-second send guard runs inside _track_open_db
    try:
        await _track_open_db(lead_id, resolved_campaign_id, metadata, email_type)
    except Exception as e:
        log(f"❌ open tracking error: {e}", force=True)

    return _pixel_response()


async def _handle_click(
    lead_id: int,
    request: Request,
    redirect:    Optional[str] = None,
    url:         Optional[str] = None,
    campaign_id: Optional[int] = None,
) -> Response:
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


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/open/{lead_id}")
async def open_pixel(
    lead_id:     int,
    request:     Request,
    campaign_id: Optional[int] = Query(None),
    email_type:  Optional[str] = Query(None),
):
    return await _handle_open(lead_id, request, campaign_id, email_type)


@app.get("/track/open")
async def open_pixel_legacy(
    lead_id:     int           = Query(..., ge=1),
    request:     Request       = None,
    campaign_id: Optional[int] = Query(None),
    email_type:  Optional[str] = Query(None),
):
    return await _handle_open(lead_id, request, campaign_id, email_type)


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
