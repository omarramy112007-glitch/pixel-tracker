# outreach_engine/tracking/pixel_server.py

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from outreach_engine.database.supabase_client import supabase

DEBUG_LOGS = os.getenv("PIXEL_DEBUG_LOGS", "false").strip().lower() == "true"

# ── Burst window only — blocks rapid-fire retries of the same open event
# After this window expires the same lead can open again and it will count
OPEN_BURST_SECONDS  = int(os.getenv("OPEN_BURST_SECONDS",  "3"))

# ── Click dedup — longer window to avoid double-counting accidental clicks
CLICK_DEDUP_SECONDS = int(os.getenv("CLICK_DEDUP_SECONDS", "300"))

# ── Global cooldown — after ANY open is accepted, reject ALL opens
# for this many seconds regardless of lead_id or email_type.
# This prevents the same pixel event from sneaking through before
# the write + correction cycle finishes.
GLOBAL_OPEN_COOLDOWN_SECONDS = int(os.getenv("GLOBAL_OPEN_COOLDOWN_SECONDS", "2"))


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

# ── Open burst cache — key: "burst:{lead_id}:{email_type}" → monotonic time
# Short TTL (3s) — only blocks rapid-fire retries, not real later opens
OPEN_BURST_CACHE: Dict[str, float] = {}

# ── Click cache — key: sha1(click:lead_id:url:day) → wall-clock timestamp
CLICK_CACHE: Dict[str, float] = {}

# ── Global cooldown — monotonic timestamp of the last accepted open
# Any open within GLOBAL_OPEN_COOLDOWN_SECONDS of this is rejected
_last_open_accepted_at: float = 0.0

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


# ---------------------------------------------------------------------------
# Basic routes
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"status": "ok", "service": "pixel tracker running"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _day_bucket() -> str:
    return _utc_now().date().isoformat()


def _mono() -> float:
    return time.monotonic()


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------

def _normalize_email_type(raw: Optional[str]) -> str:
    """Always returns exactly 'cold' or 'followup'."""
    return "followup" if (raw or "").strip().lower() == "followup" else "cold"


# ---------------------------------------------------------------------------
# Global cooldown — rejects ALL opens for N seconds after any accepted open
# ---------------------------------------------------------------------------

def _check_global_cooldown() -> bool:
    """
    Returns True if we are WITHIN the global cooldown window.
    Returns False if we are PAST it (safe to accept).

    After ANY open is accepted, the cooldown timer starts.
    During the cooldown window ALL opens are rejected —
    regardless of lead_id, email_type, campaign_id, etc.
    """
    global _last_open_accepted_at
    now = _mono()
    elapsed = now - _last_open_accepted_at

    if elapsed < GLOBAL_OPEN_COOLDOWN_SECONDS:
        log(
            f"🧊 GLOBAL COOLDOWN ACTIVE → "
            f"{elapsed:.3f}s since last open "
            f"(need {GLOBAL_OPEN_COOLDOWN_SECONDS}s gap)",
            force=True,
        )
        return True

    return False


def _mark_global_cooldown() -> None:
    """Record the monotonic timestamp of the last accepted open."""
    global _last_open_accepted_at
    _last_open_accepted_at = _mono()
    log(
        f"🧊 GLOBAL COOLDOWN STARTED → "
        f"next open allowed after {GLOBAL_OPEN_COOLDOWN_SECONDS}s",
        force=True,
    )


# ---------------------------------------------------------------------------
# Open burst dedup — short window only
# ---------------------------------------------------------------------------

def _purge_burst_cache() -> None:
    """Remove entries older than OPEN_BURST_SECONDS."""
    now     = _mono()
    expired = [k for k, t in OPEN_BURST_CACHE.items()
               if now - t > OPEN_BURST_SECONDS]
    for k in expired:
        del OPEN_BURST_CACHE[k]


def _claim_open(lead_id: int, email_type: str) -> bool:
    """
    Returns True (and claims the slot) if this open is NOT a rapid-fire
    retry within OPEN_BURST_SECONDS.

    Key is per lead + email_type only — no day bucket, no campaign_id.
    """
    now = _mono()
    _purge_burst_cache()

    key = f"burst:{lead_id}:{email_type}"

    last = OPEN_BURST_CACHE.get(key)
    if last is not None and (now - last) < OPEN_BURST_SECONDS:
        return False

    OPEN_BURST_CACHE[key] = now
    return True


# ---------------------------------------------------------------------------
# Click dedup — longer window
# ---------------------------------------------------------------------------

def _purge_click_cache() -> None:
    now_ts  = _utc_now().timestamp()
    expired = [k for k, ts in CLICK_CACHE.items()
               if (now_ts - ts) > CLICK_DEDUP_SECONDS]
    for k in expired:
        CLICK_CACHE.pop(k, None)


def _claim_click(lead_id: int, url: str) -> bool:
    now_ts = _utc_now().timestamp()
    _purge_click_cache()

    raw = f"click:{lead_id}:{url}:{_day_bucket()}"
    key = hashlib.sha1(raw.encode()).hexdigest()

    if key in CLICK_CACHE and (now_ts - CLICK_CACHE[key]) < CLICK_DEDUP_SECONDS:
        return False

    CLICK_CACHE[key] = now_ts
    return True


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _resolve_campaign_id(lead_id: int) -> Optional[int]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("campaign_id")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            log(f"⚠ lead_id={lead_id} not found in outreach_leads", force=True)
            return None
        cid = res.data[0].get("campaign_id")
        return int(cid) if cid is not None else None
    except Exception as e:
        log(f"⚠ campaign resolve error lead={lead_id}: {e}", force=True)
    return None


def _read_open_counters(lead_id: int) -> Dict[str, int]:
    """Read both open counters from Supabase for a given lead."""
    try:
        res = (
            supabase.table("outreach_leads")
            .select("open_count, followup_open_count")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if res.data:
            row = res.data[0]
            return {
                "open_count":          int(row.get("open_count") or 0),
                "followup_open_count": int(row.get("followup_open_count") or 0),
            }
    except Exception as e:
        log(f"⚠ _read_open_counters failed lead={lead_id}: {e}", force=True)
    return {"open_count": 0, "followup_open_count": 0}


def _write_lead_event(
    lead_id:     int,
    campaign_id: Optional[int],
    event_type:  str,
    metadata:    Dict[str, Any],
) -> None:
    payload: Dict[str, Any] = {
        "lead_id":    str(lead_id),
        "event_type": event_type,
        "timestamp":  _utc_iso(),
        "metadata":   {**metadata, "outreach_lead_id": lead_id},
    }
    if campaign_id:
        payload["campaign_id"] = campaign_id
    try:
        supabase.table("lead_events").insert(payload).execute()
    except Exception as e:
        msg = str(e).lower()
        if "campaign_id" in msg or "schema cache" in msg or "does not exist" in msg:
            fallback = dict(payload)
            fallback.pop("campaign_id", None)
            supabase.table("lead_events").insert(fallback).execute()
        elif "duplicate key" not in msg:
            log(f"⚠ lead_events insert failed lead={lead_id}: {e}", force=True)


def _increment_crm(lead_id: int, field: str) -> None:
    res = (
        supabase.table("crm_analytics")
        .select("*")
        .eq("lead_id", str(lead_id))
        .limit(1)
        .execute()
    )
    now             = _utc_iso()
    engagement_bump = 2 if field == "opens" else 3 if field == "clicks" else 0

    if res.data:
        row = res.data[0]
        supabase.table("crm_analytics").update({
            field:              int(row.get(field) or 0) + 1,
            "engagement_score": float(row.get("engagement_score") or 0) + engagement_bump,
            "last_activity":    now,
        }).eq("lead_id", str(lead_id)).execute()
    else:
        supabase.table("crm_analytics").insert({
            "lead_id":          str(lead_id),
            field:              1,
            "engagement_score": engagement_bump,
            "last_activity":    now,
        }).execute()


# ---------------------------------------------------------------------------
# _write_open — with snapshot/correct logic
# ---------------------------------------------------------------------------

def _write_open(lead_id: int, email_type: str, campaign_id: Optional[int]) -> None:
    now = _utc_iso()

    # ── Step 1: Read both counters BEFORE any write ───────────────────────
    try:
        res = (
            supabase.table("outreach_leads")
            .select(
                "open_count, followup_open_count, email_opened, campaign_id"
            )
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            log(f"⚠ _write_open: lead_id={lead_id} not found", force=True)
            return
    except Exception as e:
        log(f"❌ _write_open initial read failed lead={lead_id}: {e}", force=True)
        return

    row          = res.data[0]
    resolved_cid = campaign_id if campaign_id is not None else row.get("campaign_id")

    snapshot_open          = int(row.get("open_count") or 0)
    snapshot_followup_open = int(row.get("followup_open_count") or 0)

    log(
        f"📊 SNAPSHOT BEFORE write → lead={lead_id} "
        f"open_count={snapshot_open} "
        f"followup_open_count={snapshot_followup_open} "
        f"type={email_type}",
        force=True,
    )

    # ── Build base update fields ──────────────────────────────────────────
    update: Dict[str, Any] = {
        "email_opened": True,
        "last_updated": now,
    }
    if not row.get("email_opened"):
        update["email_opened_at"] = now

    # =====================================================================
    #  COLD EMAIL PATH
    # =====================================================================
    if email_type == "cold":
        try:
            # Step 2: Increment open_count → write
            new = snapshot_open + 1
            update["open_count"] = new
            supabase.table("outreach_leads").update(update).eq(
                "id", lead_id
            ).execute()
            log(
                f"📬 COLD STEP 1 → open_count {snapshot_open} → {new} "
                f"(written to Supabase)",
                force=True,
            )
        except Exception as e:
            log(
                f"❌ COLD STEP 1 write failed lead={lead_id}: {e}",
                force=True,
            )
            return

        try:
            # Step 3: Wait 1 second
            time.sleep(1)
            log(
                f"⏳ COLD STEP 2 → waited 1 second",
                force=True,
            )
        except Exception:
            pass

        try:
            # Step 4: Re-read BOTH counters from Supabase
            live = _read_open_counters(lead_id)
            live_open          = live["open_count"]
            live_followup_open = live["followup_open_count"]

            log(
                f"📊 COLD STEP 3 → LIVE from Supabase → "
                f"open_count={live_open} "
                f"followup_open_count={live_followup_open}",
                force=True,
            )
        except Exception as e:
            log(
                f"❌ COLD STEP 3 read failed lead={lead_id}: {e}",
                force=True,
            )
            return

        try:
            # Step 5: Decrement open_count by 1
            corrected = live_open - 1
            supabase.table("outreach_leads").update({
                "open_count":   corrected,
                "last_updated": _utc_iso(),
            }).eq("id", lead_id).execute()
            log(
                f"🔧 COLD STEP 4 → open_count corrected "
                f"{live_open} → {corrected}",
                force=True,
            )
        except Exception as e:
            log(
                f"❌ COLD STEP 4 correction failed lead={lead_id}: {e}",
                force=True,
            )
            return

        try:
            # Step 6: Check if followup_open_count changed
            if live_followup_open != snapshot_followup_open:
                supabase.table("outreach_leads").update({
                    "followup_open_count": snapshot_followup_open,
                    "last_updated":        _utc_iso(),
                }).eq("id", lead_id).execute()
                log(
                    f"🔧 COLD STEP 5 → followup_open_count RESTORED "
                    f"{live_followup_open} → {snapshot_followup_open}",
                    force=True,
                )
            else:
                log(
                    f"✅ COLD STEP 5 → followup_open_count UNCHANGED "
                    f"= {snapshot_followup_open}",
                    force=True,
                )
        except Exception as e:
            log(
                f"❌ COLD STEP 5 check failed lead={lead_id}: {e}",
                force=True,
            )

    # =====================================================================
    #  FOLLOWUP EMAIL PATH
    # =====================================================================
    else:
        try:
            # Step 2: Increment followup_open_count → write
            new = snapshot_followup_open + 1
            update["followup_open_count"] = new
            supabase.table("outreach_leads").update(update).eq(
                "id", lead_id
            ).execute()
            log(
                f"📬 FOLLOWUP STEP 1 → followup_open_count "
                f"{snapshot_followup_open} → {new} "
                f"(written to Supabase)",
                force=True,
            )
        except Exception as e:
            log(
                f"❌ FOLLOWUP STEP 1 write failed lead={lead_id}: {e}",
                force=True,
            )
            return

        try:
            # Step 3: Re-read open_count from Supabase
            live = _read_open_counters(lead_id)
            live_open          = live["open_count"]
            live_followup_open = live["followup_open_count"]

            log(
                f"📊 FOLLOWUP STEP 2 → LIVE from Supabase → "
                f"open_count={live_open} "
                f"followup_open_count={live_followup_open}",
                force=True,
            )
        except Exception as e:
            log(
                f"❌ FOLLOWUP STEP 2 read failed lead={lead_id}: {e}",
                force=True,
            )
            return

        try:
            # Step 4: Check if open_count changed
            if live_open != snapshot_open:
                supabase.table("outreach_leads").update({
                    "open_count":   snapshot_open,
                    "last_updated": _utc_iso(),
                }).eq("id", lead_id).execute()
                log(
                    f"🔧 FOLLOWUP STEP 3 → open_count RESTORED "
                    f"{live_open} → {snapshot_open}",
                    force=True,
                )
            else:
                log(
                    f"✅ FOLLOWUP STEP 3 → open_count UNCHANGED "
                    f"= {snapshot_open}",
                    force=True,
                )
        except Exception as e:
            log(
                f"❌ FOLLOWUP STEP 3 check failed lead={lead_id}: {e}",
                force=True,
            )

    # ── Event + CRM (best-effort, independent of counter logic) ──────────
    try:
        _write_lead_event(lead_id, resolved_cid, "opened", {
            "email_type": email_type,
            "channel":    "email",
        })
    except Exception as e:
        log(f"⚠ _write_lead_event failed (non-fatal) lead={lead_id}: {e}", force=True)

    try:
        _increment_crm(lead_id, "opens")
    except Exception as e:
        log(f"⚠ _increment_crm failed (non-fatal) lead={lead_id}: {e}", force=True)

    log(
        f"🏁 _write_open DONE → lead={lead_id} type={email_type}",
        force=True,
    )


# ---------------------------------------------------------------------------
# Click handler
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

        row          = res.data[0]
        resolved_cid = campaign_id if campaign_id is not None else row.get("campaign_id")
        new_count    = int(row.get("click_count") or 0) + 1

        supabase.table("outreach_leads").update({
            "click_count":  new_count,
            "link_clicked": True,
            "last_updated": now,
        }).eq("id", lead_id).execute()

        log(f"🖱 click_count → lead={lead_id} → {new_count}", force=True)

    except Exception as e:
        log(f"❌ _write_click failed lead={lead_id}: {e}", force=True)
        return

    try:
        _write_lead_event(lead_id, resolved_cid, "clicked", {"channel": "email"})
    except Exception as e:
        log(f"⚠ _write_lead_event failed (non-fatal) lead={lead_id}: {e}", force=True)

    try:
        _increment_crm(lead_id, "clicks")
    except Exception as e:
        log(f"⚠ _increment_crm failed (non-fatal) lead={lead_id}: {e}", force=True)


# ---------------------------------------------------------------------------
# Open handler — with 3 layers of dedup
# ---------------------------------------------------------------------------

async def _handle_open(
    lead_id:     int,
    request:     Request,
    campaign_id: Optional[int] = None,
    email_type:  str           = "cold",
) -> Response:
    et           = _normalize_email_type(email_type)
    resolved_cid = campaign_id if campaign_id is not None else _resolve_campaign_id(lead_id)

    log(f"🔍 OPEN lead={lead_id} type={et} campaign={resolved_cid}", force=True)

    # ── Layer 1: Global cooldown — blocks ALL opens for N seconds ─────────
    async with PROCESS_LOCK:
        if _check_global_cooldown():
            log(
                f"🧊 GLOBAL COOLDOWN REJECTED → lead={lead_id} type={et} "
                f"(waiting for cooldown to expire)",
                force=True,
            )
            return _pixel_response()

    # ── Layer 2: Per-lead per-type burst — blocks same event within 3s ───
    async with PROCESS_LOCK:
        is_new = _claim_open(lead_id, et)

    if not is_new:
        log(
            f"🧠 Burst duplicate blocked → lead={lead_id} type={et} "
            f"(same open event fired again within {OPEN_BURST_SECONDS}s)",
            force=True,
        )
        return _pixel_response()

    # ── Layer 3: Accept + write + start global cooldown ───────────────────
    log(f"✅ Open accepted lead={lead_id} type={et}", force=True)

    # Mark global cooldown BEFORE writing so any pixel that arrives
    # during the write + correction cycle is rejected
    _mark_global_cooldown()

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
        lead_id,
        request,
        campaign_id,
        email_type=_normalize_email_type(email_type),
    )


@app.get("/track/open")
async def open_pixel_legacy(
    lead_id:     int           = Query(..., ge=1),
    request:     Request       = None,
    campaign_id: Optional[int] = Query(None),
    email_type:  Optional[str] = Query(None),
):
    return await _handle_open(
        lead_id,
        request,
        campaign_id,
        email_type=_normalize_email_type(email_type),
    )


# ---------------------------------------------------------------------------
# Click handler
# ---------------------------------------------------------------------------

async def _handle_click(
    lead_id:     int,
    request:     Request,
    redirect:    Optional[str] = None,
    url:         Optional[str] = None,
    campaign_id: Optional[int] = None,
) -> Response:
    safe_url     = _safe_redirect_url(redirect or url)
    resolved_cid = campaign_id if campaign_id is not None else _resolve_campaign_id(lead_id)

    async with PROCESS_LOCK:
        is_new = _claim_click(lead_id, safe_url or "")

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
