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

DEBUG_LOGS = os.getenv("PIXEL_DEBUG_LOGS", "false").strip().lower() == "true"

OPEN_DEDUP_SECONDS  = int(os.getenv("OPEN_DEDUP_SECONDS",  "86400"))  # 24h per type per lead
CLICK_DEDUP_SECONDS = int(os.getenv("CLICK_DEDUP_SECONDS", "300"))


def log(*args, force: bool = False) -> None:
    if force or DEBUG_LOGS:
        print(*args)


log("🔥 PIXEL SERVER LOADED", force=True)

app = FastAPI(title="Outreach Engine Pixel Tracker")

try:
    from outreach_engine.tracking.gmail_webhook import router as gmail_router
    app.include_router(gmail_router, prefix="/gmail")
    log("✅ Gmail router mounted", force=True)
except Exception as e:
    log(f"⚠ Gmail router disabled: {e}", force=True)

PROCESS_LOCK = asyncio.Lock()

# Keyed by "open:{lead_id}:{email_type}:{day}" → timestamp
OPEN_CACHE:  Dict[str, float] = {}
CLICK_CACHE: Dict[str, float] = {}

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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _day_bucket() -> str:
    return _utc_now().date().isoformat()


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


def _normalize_email_type(raw: Optional[str]) -> str:
    """Always returns 'cold' or 'followup' — nothing else."""
    return "followup" if (raw or "").strip().lower() == "followup" else "cold"


def _cleanup_cache(cache: Dict[str, float], ttl: int) -> None:
    now_ts  = _utc_now().timestamp()
    expired = [k for k, ts in cache.items() if (now_ts - ts) > ttl]
    for k in expired:
        cache.pop(k, None)


def _remember(cache: Dict[str, float], key: str, ttl: int) -> bool:
    """Returns True (and records) if this key has not been seen within ttl."""
    now_ts = _utc_now().timestamp()
    _cleanup_cache(cache, ttl)
    if key in cache and (now_ts - cache[key]) < ttl:
        return False
    cache[key] = now_ts
    return True


def _make_open_key(lead_id: int, email_type: str, campaign_id: Optional[int]) -> str:
    """
    Dedup key includes email_type so cold and followup are independent.
    One cold open and one followup open are each allowed per lead per day.
    """
    cid = str(campaign_id) if campaign_id is not None else "none"
    return f"open:{lead_id}:{email_type}:{cid}:{_day_bucket()}"


def _make_click_key(lead_id: int, url: str) -> str:
    raw = f"click:{lead_id}:{url}:{_day_bucket()}"
    return hashlib.sha1(raw.encode()).hexdigest()


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


def _write_open(lead_id: int, email_type: str, campaign_id: Optional[int]) -> None:
    """
    Routes to the correct counter:
      email_type == 'cold'     → open_count += 1
      email_type == 'followup' → followup_open_count += 1

    Both also set email_opened = True.
    No cross-counter writes.
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
            log(f"⚠ _write_open: lead_id={lead_id} not found", force=True)
            return

        row         = res.data[0]
        resolved_cid = campaign_id or row.get("campaign_id")

        update: Dict[str, Any] = {
            "email_opened": True,
            "last_updated": now,
        }
        if not row.get("email_opened"):
            update["email_opened_at"] = now

        if email_type == "followup":
            current = int(row.get("followup_open_count") or 0)
            update["followup_open_count"] = current + 1
            log(f"📬 followup_open_count → lead={lead_id} {current}→{current+1}", force=True)
        else:
            current = int(row.get("open_count") or 0)
            update["open_count"] = current + 1
            log(f"📬 open_count → lead={lead_id} {current}→{current+1}", force=True)

        supabase.table("outreach_leads").update(update).eq("id", lead_id).execute()

        _write_lead_event(lead_id, resolved_cid, "opened", {
            "email_type": email_type,
            "channel":    "email",
        })
        _increment_crm(lead_id, "opens")

    except Exception as e:
        log(f"❌ _write_open failed lead={lead_id}: {e}", force=True)


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

        row          = res.data[0]
        resolved_cid = campaign_id or row.get("campaign_id")
        new_count    = int(row.get("click_count") or 0) + 1

        supabase.table("outreach_leads").update({
            "click_count":  new_count,
            "link_clicked": True,
            "last_updated": now,
        }).eq("id", lead_id).execute()

        _write_lead_event(lead_id, resolved_cid, "clicked", {"channel": "email"})
        _increment_crm(lead_id, "clicks")
        log(f"🖱 click_count → lead={lead_id} →{new_count}", force=True)

    except Exception as e:
        log(f"❌ _write_click failed lead={lead_id}: {e}", force=True)


def _write_lead_event(
    lead_id:     int,
    campaign_id: Optional[int],
    event_type:  str,
    metadata:    Dict[str, Any],
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
            log(f"⚠ lead_events insert failed: {e}", force=True)


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
        engagement_bump = 2 if field == "opens" else 3 if field == "clicks" else 0

        if res.data:
            row = res.data[0]
            supabase.table("crm_analytics").update({
                field:             int(row.get(field) or 0) + 1,
                "engagement_score": float(row.get("engagement_score") or 0) + engagement_bump,
                "last_activity":   now,
            }).eq("lead_id", str(lead_id)).execute()
        else:
            supabase.table("crm_analytics").insert({
                "lead_id":          str(lead_id),
                field:              1,
                "engagement_score": engagement_bump,
                "last_activity":    now,
            }).execute()
    except Exception as e:
        log(f"⚠ crm_analytics update failed: {e}", force=True)


async def _handle_open(
    lead_id:     int,
    request:     Request,
    campaign_id: Optional[int] = None,
    email_type:  str = "cold",
) -> Response:
    et               = _normalize_email_type(email_type)
    resolved_cid     = campaign_id or _resolve_campaign_id(lead_id)

    log(f"🔍 OPEN lead={lead_id} type={et} campaign={resolved_cid}", force=True)

    if resolved_cid is None:
        return _pixel_response()

    dedup_key = _make_open_key(lead_id, et, resolved_cid)

    async with PROCESS_LOCK:
        is_new = _remember(OPEN_CACHE, dedup_key, OPEN_DEDUP_SECONDS)

    if not is_new:
        log(f"🧠 Duplicate open blocked lead={lead_id} type={et}", force=True)
        return _pixel_response()

    log(f"✅ Open accepted lead={lead_id} type={et}", force=True)
    _write_open(lead_id, et, resolved_cid)
    return _pixel_response()


@app.get("/open/{lead_id}")
async def open_pixel(
    lead_id:     int,
    request:     Request,
    campaign_id: Optional[int] = Query(None),
    email_type:  Optional[str] = Query(None),
    ts:          Optional[int] = Query(None),
    t:           Optional[str] = Query(None),
):
    return await _handle_open(
        lead_id, request, campaign_id,
        email_type=_normalize_email_type(email_type),
    )


@app.get("/track/open")
async def open_pixel_legacy(
    lead_id:     int             = Query(..., ge=1),
    request:     Request         = None,
    campaign_id: Optional[int]   = Query(None),
    email_type:  Optional[str]   = Query(None),
):
    return await _handle_open(
        lead_id, request, campaign_id,
        email_type=_normalize_email_type(email_type),
    )


async def _handle_click(
    lead_id:     int,
    request:     Request,
    redirect:    Optional[str] = None,
    url:         Optional[str] = None,
    campaign_id: Optional[int] = None,
) -> Response:
    safe_url     = _safe_redirect_url(redirect or url)
    resolved_cid = campaign_id or _resolve_campaign_id(lead_id)
    dedup_key    = _make_click_key(lead_id, safe_url or "")

    async with PROCESS_LOCK:
        is_new = _remember(CLICK_CACHE, dedup_key, CLICK_DEDUP_SECONDS)

    if not is_new:
        log(f"🧠 Duplicate click blocked lead={lead_id}", force=True)
        if safe_url:
            return RedirectResponse(url=safe_url)
        return JSONResponse({"status": "ok"})

    _write_click(lead_id, resolved_cid)

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
