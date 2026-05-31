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

OPEN_DEDUP_SECONDS       = int(os.getenv("OPEN_DEDUP_SECONDS", "900"))
CLICK_DEDUP_SECONDS      = int(os.getenv("CLICK_DEDUP_SECONDS", "300"))
MIN_SEND_TO_OPEN_SECONDS = int(os.getenv("MIN_SEND_TO_OPEN_SECONDS", "2"))

_RAW_BLOCKED_IPS = os.getenv("PIXEL_BLOCKED_IPS", "").strip()
BLOCKED_IPS: set[str] = (
    {ip.strip() for ip in _RAW_BLOCKED_IPS.split(",") if ip.strip()}
    if _RAW_BLOCKED_IPS
    else set()
)

BOT_UA_PATTERNS = [
    "googlebot",
    "google-apps-script",
    "google-read-aloud",
    "apis-google",
    "feedfetcher-google",
    "msnbot",
    "bingbot",
    "barracudacentral",
    "proofpoint",
    "mimecast",
    "symantec",
    "sophos",
    "trend micro",
    "cloudmark",
    "spamhaus",
    "postfix",
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


log("📦 REGISTERED ROUTES:", force=True)
for route in app.routes:
    path    = getattr(route, "path", None)
    methods = getattr(route, "methods", None)
    if path:
        log(f"  {path} {list(methods or [])}", force=True)

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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_ts() -> float:
    return _utc_now().timestamp()


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
    now_ts  = _utc_now_ts()
    expired = [k for k, ts in cache.items() if (now_ts - ts) > ttl_seconds]
    for k in expired:
        cache.pop(k, None)


def _remember(cache: Dict[str, float], key: str, ttl_seconds: int) -> bool:
    now_ts = _utc_now_ts()
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
    email_type: Optional[str],
    last_email_sent: Optional[str],
    followup_status: Optional[str] = None,
) -> str:
    """
    Dedup fingerprint for an open event.

    Includes email_type + followup_status + last_email_sent so that
    cold and follow-up opens never share a key, and opens from different
    send rounds (different last_email_sent) each get their own slot.
    """
    cid  = str(campaign_id) if campaign_id is not None else "none"
    et   = (email_type or "none").strip().lower()
    fs   = (followup_status or "none").strip().lower()
    sent = str(last_email_sent).strip() if last_email_sent else _day_bucket()
    return hashlib.sha1(
        f"open:{lead_id}:{cid}:{et}:{fs}:{sent}".encode()
    ).hexdigest()


def _make_click_fingerprint(
    lead_id: int,
    url: str,
    metadata: Dict[str, Any],
) -> str:
    ua        = (metadata.get("user_agent") or "").lower().strip()
    day       = _day_bucket()
    clean_url = _safe_redirect_url(url) or url.strip()
    return hashlib.sha1(
        f"click:{lead_id}:{clean_url}:{day}:{ua}".encode()
    ).hexdigest()


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
    Fetch fields needed for tracking in one DB call.
    NOTE: 'status' is intentionally excluded — it caused query failures
    on some Supabase schema cache states and is not needed for tracking.
    All decisions use followup_status + followup_step instead.
    """
    try:
        res = (
            supabase.table("outreach_leads")
            .select(
                "campaign_id, last_email_sent, followup_status, "
                "followup_step, email, email_opened, open_count, "
                "followup_open_count"
            )
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
            "lead_id":     system_lead_id,
            "campaign_id": campaign_id,
            "event_type":  event_type,
            "metadata":    metadata,
            "timestamp":   _utc_now().isoformat(),
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
            row = existing.data[0]
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
            supabase.table("crm_analytics").update(payload).eq(
                "lead_id", system_lead_id
            ).execute()
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


def _is_bot_request(request: Optional[Request]) -> bool:
    if not request:
        return True

    client_ip = request.client.host if request.client else None
    if client_ip and client_ip in BLOCKED_IPS:
        log(f"🚫 Open blocked — sender IP: {client_ip}", force=True)
        return True

    ua = (request.headers.get("user-agent") or "").lower().strip()
    if not ua:
        log("🚫 Open blocked — empty User-Agent", force=True)
        return True

    for pattern in BOT_UA_PATTERNS:
        if pattern in ua:
            log(
                f"🚫 Open blocked — bot UA matched '{pattern}': {ua[:120]}",
                force=True,
            )
            return True

    return False


def _open_is_too_soon(
    send_ts: Optional[int],
    last_email_sent: Optional[str],
    lead_id: int,
) -> bool:
    """
    Timing guard — three priority levels.

    Priority 1 — URL ts (send_ts):
        Set at build time in outreach_sender before the Gmail call.
        elapsed >= threshold → return False (allow) immediately.
        elapsed <  threshold → return True  (block) immediately.

    Priority 2 — DB last_email_sent:
        Fallback for emails without ts in the URL.
        Same allow/block logic.

    Priority 3 — No info at all:
        Block by default to avoid counting bot prefetches.
    """
    now_ts = _utc_now_ts()

    # Priority 1 — URL timestamp
    if send_ts is not None:
        try:
            elapsed = now_ts - float(send_ts)
            if elapsed < MIN_SEND_TO_OPEN_SECONDS:
                log(
                    f"⏳ Open ignored — too soon after send "
                    f"({elapsed:.1f}s < {MIN_SEND_TO_OPEN_SECONDS}s) "
                    f"→ lead_id={lead_id}",
                    force=True,
                )
                return True
            # Elapsed is fine — allow without hitting DB guard
            return False
        except Exception as e:
            log(f"⚠ ts parse error for lead {lead_id}: {e}", force=True)
            # Fall through to Priority 2

    # Priority 2 — DB timestamp
    if last_email_sent:
        try:
            sent_time = datetime.fromisoformat(
                str(last_email_sent).replace("Z", "+00:00")
            )
            if sent_time.tzinfo is None:
                sent_time = sent_time.replace(tzinfo=timezone.utc)
            elapsed = now_ts - sent_time.timestamp()
            if elapsed < MIN_SEND_TO_OPEN_SECONDS:
                log(
                    f"⏳ Open ignored (DB guard) — too soon after send "
                    f"({elapsed:.1f}s < {MIN_SEND_TO_OPEN_SECONDS}s) "
                    f"→ lead_id={lead_id}",
                    force=True,
                )
                return True
            return False
        except Exception as e:
            log(f"⚠ DB timestamp parse error for lead {lead_id}: {e}", force=True)
            # Fall through to Priority 3

    # Priority 3 — no timing info → block
    log(
        f"⏳ Open ignored — no timing info available → lead_id={lead_id}",
        force=True,
    )
    return True


def _resolve_email_type(
    email_type: Optional[str],
    lead_meta: Dict[str, Any],
) -> str:
    """
    Canonical email type for counter routing.

    Priority 1 — explicit URL param: always trust "cold" or "followup".
    Priority 2 — followup_status from DB: "no_open"/"soft_open" → followup.
    Priority 3 — followup_step from DB: step > 0 → followup.
    Priority 4 — default: "cold".

    This covers legacy emails that pre-date the email_type URL param.
    """
    if email_type:
        et = email_type.strip().lower()
        if et in {"cold", "followup"}:
            return et

    try:
        followup_status = (lead_meta.get("followup_status") or "").strip().lower()
        if followup_status in {"no_open", "soft_open"}:
            return "followup"

        followup_step = int(lead_meta.get("followup_step") or 0)
        if followup_step > 0:
            return "followup"
    except Exception:
        pass

    return "cold"


async def _track_open_db(
    lead_id: int,
    campaign_id: int,
    metadata: Dict[str, Any],
    email_type: Optional[str],
    lead_meta: Dict[str, Any],
) -> None:
    try:
        if not lead_meta:
            log(f"⚠ Lead {lead_id} not found for open tracking", force=True)
            return

        email = (lead_meta.get("email") or "").strip().lower() or None
        now   = _utc_now().isoformat()

        # Resolve canonical type — URL param wins, DB state is fallback
        resolved_type = _resolve_email_type(email_type, lead_meta)

        updates: Dict[str, Any] = {
            "last_updated": now,
            "email_opened": True,
        }
        if not lead_meta.get("email_opened"):
            updates["email_opened_at"] = now

        if resolved_type == "followup":
            updates["followup_open_count"] = (
                int(lead_meta.get("followup_open_count") or 0) + 1
            )
            log(
                f"📬 followup_open_count++ → lead_id={lead_id} "
                f"(url_email_type={email_type} resolved={resolved_type} "
                f"followup_status={lead_meta.get('followup_status')} "
                f"followup_step={lead_meta.get('followup_step')})",
                force=True,
            )
        else:
            updates["open_count"] = int(lead_meta.get("open_count") or 0) + 1
            log(
                f"📬 open_count++ → lead_id={lead_id} "
                f"(url_email_type={email_type} resolved={resolved_type})",
                force=True,
            )

        supabase.table("outreach_leads").update(updates).eq(
            "id", lead_id
        ).execute()

        system_lead_id = _resolve_system_lead_id_from_email(email)
        event_metadata = {
            **metadata,
            "ts":               now,
            "channel":          "email",
            "source":           "pixel",
            "campaign_id":      campaign_id,
            "outreach_lead_id": lead_id,
            "email_type":       resolved_type,
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
        supabase.table("outreach_leads").update(updates).eq(
            "id", lead_id
        ).execute()

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

        log(f"🖱 CLICK TRACKED → lead_id={lead_id}", force=True)
    except Exception as e:
        log(f"❌ click tracking db error: {e}", force=True)


async def _handle_open(
    lead_id: int,
    request: Request,
    campaign_id: Optional[int] = None,
    email_type:  Optional[str] = None,
    send_ts:     Optional[int] = None,
) -> Response:

    # Step 1 — bot filter
    if _is_bot_request(request):
        return _pixel_response()

    metadata = _safe_headers(request)

    # Step 2 — resolve lead meta (one DB call, no status column)
    lead_meta = _resolve_lead_meta(lead_id)

    # Step 3 — resolve campaign_id
    resolved_campaign_id: Optional[int] = campaign_id
    if resolved_campaign_id is None:
        raw_cid = lead_meta.get("campaign_id")
        if raw_cid is not None:
            try:
                resolved_campaign_id = int(raw_cid)
            except (TypeError, ValueError):
                pass

    if resolved_campaign_id is None:
        log(
            f"⚠ No campaign_id for lead {lead_id} — open not tracked",
            force=True,
        )
        return _pixel_response()

    last_email_sent = lead_meta.get("last_email_sent")
    followup_status = (lead_meta.get("followup_status") or "").strip().lower()

    # Step 4 — timing guard
    if _open_is_too_soon(send_ts, last_email_sent, lead_id):
        return _pixel_response()

    # Step 5 — dedup
    fingerprint = _make_open_fingerprint(
        lead_id,
        resolved_campaign_id,
        email_type,
        last_email_sent,
        followup_status,
    )
    async with PROCESS_LOCK:
        if not _remember(OPEN_CACHE, fingerprint, OPEN_DEDUP_SECONDS):
            log(
                f"🧠 Duplicate open ignored → lead_id={lead_id} "
                f"(email_type={email_type} followup_status={followup_status})",
                force=True,
            )
            return _pixel_response()

    # Step 6 — persist
    try:
        await _track_open_db(
            lead_id,
            resolved_campaign_id,
            metadata,
            email_type,
            lead_meta,
        )
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
    ts:          Optional[int] = Query(None),
):
    return await _handle_open(
        lead_id,
        request,
        campaign_id,
        email_type,
        send_ts=ts,
    )


@app.get("/track/open")
async def open_pixel_legacy(
    lead_id:     int           = Query(..., ge=1),
    request:     Request       = None,
    campaign_id: Optional[int] = Query(None),
    email_type:  Optional[str] = Query(None),
    ts:          Optional[int] = Query(None),
):
    return await _handle_open(
        lead_id,
        request,
        campaign_id,
        email_type,
        send_ts=ts,
    )


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
