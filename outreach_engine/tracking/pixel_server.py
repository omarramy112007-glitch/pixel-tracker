# outreach_engine/tracking/pixel_server.py

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response

from outreach_engine.database.supabase_client import supabase

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# After the first pixel fires for a lead, ignore ALL other pixels
# for this many seconds — stops Google prefetch from firing old cached
# pixels from other emails in the same 1s window.
LEAD_OPEN_LOCKOUT_SECONDS = 1

# Per send event (ts), count only once across all time.
SEND_EVENT_DEDUP_SECONDS = 86400  # 24 hours

app = FastAPI(title="Pixel Tracker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache 1: per lead lockout — lead_id → timestamp of first pixel this burst
# Blocks ALL pixels for the lead within LEAD_OPEN_LOCKOUT_SECONDS of first
_LEAD_LOCKOUT:  Dict[int, float] = {}

# Cache 2: per send event dedup — dedup_key → timestamp
# Blocks the same ts from being counted again after lockout expires
_SEND_EVENT_SEEN: Dict[str, float] = {}

_CLICK_CACHE: Dict[str, float] = {}
_LOCK = asyncio.Lock()

PIXEL = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)

# Gmail watcher — optional
check_for_replies   = None
start_reply_polling = None
start_watch         = None

try:
    from outreach_engine.tracking.gmail_watcher import (
        check_for_replies,
        start_reply_polling,
        start_watch,
    )
except Exception:
    pass

try:
    from outreach_engine.tracking.gmail_webhook import router as gmail_router
    app.include_router(gmail_router, prefix="/gmail")
except Exception:
    pass

GMAIL_WATCH_MODE    = os.getenv("GMAIL_WATCH_MODE", "poll").strip().lower()
GMAIL_POLL_INTERVAL = int(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "60"))


@app.on_event("startup")
async def on_startup() -> None:
    if GMAIL_WATCH_MODE == "watch" and start_watch:
        try:
            start_watch()
            return
        except Exception:
            pass
    if start_reply_polling:
        try:
            asyncio.create_task(start_reply_polling(GMAIL_POLL_INTERVAL))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> float:
    return time.monotonic()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _is_bot(request: Request) -> bool:
    ua = (request.headers.get("user-agent") or "").lower()
    if not ua:
        return True
    bot_patterns = [
        "googlebot", "google-apps-script", "apis-google",
        "feedfetcher", "msnbot", "bingbot", "curl", "wget",
        "python-requests", "python-httpx", "java/", "go-http-client",
        "headlesschrome", "phantomjs",
    ]
    return any(p in ua for p in bot_patterns)


def _check_lead_lockout(lead_id: int) -> bool:
    """
    Returns True  → lead is NOT in lockout, first pixel in this burst.
    Returns False → lead IS in lockout, ignore this pixel.

    When True is returned, the lockout is set for LEAD_OPEN_LOCKOUT_SECONDS.
    All subsequent pixels for this lead within that window return False.
    """
    now = _now()

    # Clean expired lockouts
    expired = [k for k, t in _LEAD_LOCKOUT.items()
               if now - t > LEAD_OPEN_LOCKOUT_SECONDS]
    for k in expired:
        del _LEAD_LOCKOUT[k]

    if lead_id in _LEAD_LOCKOUT:
        return False  # in lockout — ignore

    _LEAD_LOCKOUT[lead_id] = now
    return True  # first pixel — count it


def _check_send_event_seen(dedup_key: str) -> bool:
    """
    Returns True  → this send event (ts) has NOT been counted before.
    Returns False → already counted, ignore.

    This is the second layer of dedup — catches the same ts firing
    again after the lockout window has expired (e.g. lead opens the
    same email hours later on a different device).
    """
    now = _now()

    # Clean expired
    expired = [k for k, t in _SEND_EVENT_SEEN.items()
               if now - t > SEND_EVENT_DEDUP_SECONDS]
    for k in expired:
        del _SEND_EVENT_SEEN[k]

    if dedup_key in _SEND_EVENT_SEEN:
        return False  # already counted

    _SEND_EVENT_SEEN[dedup_key] = now
    return True  # new send event


def _dedup_click(key: str, ttl: int = 300) -> bool:
    now = _now()
    expired = [k for k, t in _CLICK_CACHE.items() if now - t > ttl]
    for k in expired:
        del _CLICK_CACHE[k]
    if key in _CLICK_CACHE:
        return False
    _CLICK_CACHE[key] = now
    return True


def _safe_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.hostname in {"localhost", "127.0.0.1"}:
        return None
    return urlunparse(parsed)


# ---------------------------------------------------------------------------
# DB writes — open
# ---------------------------------------------------------------------------

def _write_open(lead_id: int, email_type: str) -> None:
    """
    cold     → open_count          += 1
    followup → followup_open_count += 1
    """
    now = _utc_iso()

    try:
        res = (
            supabase.table("outreach_leads")
            .select("open_count, followup_open_count, email_opened, campaign_id")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            return

        row         = res.data[0]
        campaign_id = row.get("campaign_id")

        update: Dict[str, Any] = {
            "email_opened": True,
            "last_updated": now,
        }
        if not row.get("email_opened"):
            update["email_opened_at"] = now

        if email_type == "followup":
            current = int(row.get("followup_open_count") or 0)
            update["followup_open_count"] = current + 1
            print(f"📬 followup_open_count → lead_id={lead_id} "
                  f"{current} → {current + 1}")
        else:
            current = int(row.get("open_count") or 0)
            update["open_count"] = current + 1
            print(f"📬 open_count → lead_id={lead_id} "
                  f"{current} → {current + 1}")

        supabase.table("outreach_leads").update(update).eq("id", lead_id).execute()

        _write_lead_event(lead_id, campaign_id, "opened", {
            "email_type": email_type,
            "channel":    "email",
        })

        _increment_crm(lead_id, "opens")

    except Exception as e:
        print(f"❌ _write_open failed → lead_id={lead_id}: {e}")


# ---------------------------------------------------------------------------
# DB writes — click
# ---------------------------------------------------------------------------

def _write_click(lead_id: int, campaign_id: Optional[int]) -> None:
    now = _utc_iso()
    try:
        res = (
            supabase.table("outreach_leads")
            .select("click_count, campaign_id")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            return

        row         = res.data[0]
        campaign_id = campaign_id or row.get("campaign_id")
        new_count   = int(row.get("click_count") or 0) + 1

        supabase.table("outreach_leads").update({
            "click_count":  new_count,
            "link_clicked": True,
            "last_updated": now,
        }).eq("id", lead_id).execute()

        _write_lead_event(lead_id, campaign_id, "clicked", {"channel": "email"})
        _increment_crm(lead_id, "clicks")

        print(f"🖱 click_count → lead_id={lead_id} → {new_count}")

    except Exception as e:
        print(f"❌ _write_click failed → lead_id={lead_id}: {e}")


# ---------------------------------------------------------------------------
# DB writes — shared
# ---------------------------------------------------------------------------

def _write_lead_event(
    lead_id: int,
    campaign_id: Optional[int],
    event_type: str,
    metadata: Dict[str, Any],
) -> None:
    try:
        payload: Dict[str, Any] = {
            "lead_id":    str(lead_id),
            "event_type": event_type,
            "timestamp":  _utc_iso(),
            "metadata":   {**metadata, "outreach_lead_id": lead_id},
        }
        if campaign_id:
            payload["campaign_id"] = campaign_id
        supabase.table("lead_events").insert(payload).execute()
    except Exception as e:
        if "duplicate key" not in str(e).lower():
            print(f"⚠ lead_events insert failed: {e}")


def _increment_crm(lead_id: int, field: str) -> None:
    try:
        res = (
            supabase.table("crm_analytics")
            .select("*")
            .eq("lead_id", str(lead_id))
            .limit(1)
            .execute()
        )
        now = _utc_iso()
        if res.data:
            row = res.data[0]
            supabase.table("crm_analytics").update({
                field:              int(row.get(field) or 0) + 1,
                "engagement_score": float(row.get("engagement_score") or 0) + (
                    2 if field == "opens" else 3 if field == "clicks" else 0
                ),
                "last_activity": now,
            }).eq("lead_id", str(lead_id)).execute()
        else:
            supabase.table("crm_analytics").insert({
                "lead_id":          str(lead_id),
                field:              1,
                "engagement_score": 2 if field == "opens" else 3,
                "last_activity":    now,
            }).execute()
    except Exception as e:
        print(f"⚠ crm_analytics update failed: {e}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"status": "ok", "service": "pixel tracker"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/open/{lead_id}")
async def open_pixel(
    lead_id:     int,
    request:     Request,
    campaign_id: Optional[int] = Query(None),
    email_type:  Optional[str] = Query(None),
    ts:          Optional[int] = Query(None),
    t:           Optional[str] = Query(None),
):
    print(f"🔍 OPEN → lead_id={lead_id} email_type={email_type} ts={ts}")

    if _is_bot(request):
        print(f"🚫 Bot blocked → lead_id={lead_id}")
        return _pixel_response()

    # Resolve email_type
    et = (email_type or "").strip().lower()
    if et not in {"cold", "followup"}:
        et = "cold"

    async with _LOCK:
        # Layer 1: Lead lockout — first pixel in burst wins.
        # All other pixels for this lead within 1s are ignored.
        # This is what stops Google prefetch from firing old cached
        # pixels from other emails in the same burst.
        is_first = _check_lead_lockout(lead_id)

        if not is_first:
            print(f"🧠 Lockout active → lead_id={lead_id} "
                  f"email_type={et} ignored")
            return _pixel_response()

        # Layer 2: Send event dedup — same ts never counted twice.
        # Catches the same email being re-opened hours later on
        # a different device (lockout expired but ts already counted).
        if ts is not None:
            dedup_key = f"open:{lead_id}:{ts}"
        else:
            day       = datetime.now(timezone.utc).date().isoformat()
            dedup_key = f"open:{lead_id}:{et}:{day}"

        is_new_send_event = _check_send_event_seen(dedup_key)

        if not is_new_send_event:
            print(f"🧠 Send event already counted → lead_id={lead_id} ts={ts}")
            return _pixel_response()

    print(f"✅ Open accepted → lead_id={lead_id} email_type={et}")
    _write_open(lead_id, et)

    return _pixel_response()


@app.get("/click/{lead_id}")
async def click_pixel(
    lead_id:     int,
    request:     Request,
    campaign_id: Optional[int] = Query(None),
    redirect:    Optional[str] = Query(None),
    url:         Optional[str] = Query(None),
):
    print(f"🔍 CLICK → lead_id={lead_id}")

    safe_url  = _safe_url(redirect or url)
    dedup_key = f"click:{lead_id}:{safe_url}"

    async with _LOCK:
        is_new = _dedup_click(dedup_key)

    if not is_new:
        print(f"🧠 Duplicate click ignored → lead_id={lead_id}")
        if safe_url:
            return RedirectResponse(url=safe_url)
        return JSONResponse({"status": "ok"})

    _write_click(lead_id, campaign_id)

    if safe_url:
        return RedirectResponse(url=safe_url)
    return JSONResponse({"status": "ok"})


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


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "outreach_engine.tracking.pixel_server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
