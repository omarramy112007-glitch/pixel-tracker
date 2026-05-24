# outreach_engine/tracking/link_tracker.py
"""
Link Tracker — Analytics Only.

Responsibilities:
  - Track link clicks and redirect the user
  - Log click events to lead_events (via event_repository)
  - Deduplicate clicks (in-memory + DB-backed)
  - DO NOT trigger follow-up decisions
  - DO NOT change lead status

Rule:
  click = analytics only (100%)
  Routing is the job of event_router, not link_tracker.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import unquote

from fastapi import APIRouter, Request, Query, FastAPI
from fastapi.responses import RedirectResponse, JSONResponse

from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase

router = APIRouter()

# ---------------------------------------------------------------------------
# Deduplication cache
# ---------------------------------------------------------------------------

CLICK_CACHE: Dict[str, float] = {}
CLICK_DEDUP_SECONDS = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_url(url: str) -> str:
    return (url or "").strip()


def _clean_url(url: str) -> str:
    return _normalize_url(url).split("?")[0].split("#")[0].rstrip("/")


def _url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _build_click_key(lead_id: int, url: str) -> str:
    day_str = datetime.utcnow().date().isoformat()
    return f"{lead_id}:click:{day_str}:{_url_hash(_clean_url(url))}"


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
        print(f"⚠️ Failed to resolve campaign_id for click: {e}")
    return None


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _is_duplicate_in_memory(lead_id: int, url: str) -> bool:
    """In-memory dedup within the CLICK_DEDUP_SECONDS window."""
    now = time.time()
    key = _build_click_key(lead_id, url)
    last_seen = CLICK_CACHE.get(key)
    if last_seen and (now - last_seen) < CLICK_DEDUP_SECONDS:
        return True
    CLICK_CACHE[key] = now
    return False


def _is_duplicate_in_db(lead_id: int, url: str) -> bool:
    """
    DB-backed dedup — protects against duplicate counting after restarts.
    Checks the last 100 click events for the same URL hash.
    """
    url_hash = _url_hash(_clean_url(url))
    try:
        from outreach_engine.database.event_repository import get_events_for_lead
        events = get_events_for_lead(lead_id) or []
        for event in reversed(events[-100:]):
            if (event.get("event_type") or "").lower() != "clicked":
                continue
            metadata = event.get("metadata") or {}
            if metadata.get("click_hash") == url_hash:
                return True
    except Exception as e:
        print(f"⚠️ DB click dedup check failed: {e}")
    return False


# ---------------------------------------------------------------------------
# Core: record a click
# ---------------------------------------------------------------------------

def _record_click(
    lead_id: int,
    campaign_id: Optional[int],
    metadata: Dict[str, Any],
) -> None:
    """
    Record a click event for analytics purposes only.
    Does NOT trigger any follow-up logic.
    """
    url = _normalize_url(metadata.get("url") or "")
    if not url:
        print("⚠️ Click ignored: missing url")
        return

    clean_url = _clean_url(url)

    if _is_duplicate_in_memory(lead_id, clean_url):
        print(f"🧠 Duplicate click ignored (memory) → lead_id={lead_id}")
        return

    if _is_duplicate_in_db(lead_id, clean_url):
        print(f"🧠 Duplicate click ignored (DB) → lead_id={lead_id}")
        return

    click_date = datetime.utcnow().date().isoformat()
    click_hash = _url_hash(clean_url)

    event_metadata: Dict[str, Any] = {
        **metadata,
        "url": clean_url,
        "click_date": click_date,
        "click_hash": click_hash,
        "channel": "email",
        "source": "link_tracker",
    }

    try:
        # Store the event for analytics — this is all we do
        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="clicked",
            metadata=event_metadata,
        )
        print(f"🖱️ Click tracked (analytics) → lead_id={lead_id} | campaign_id={campaign_id}")

    except Exception as e:
        print(f"❌ Click tracking error: {e}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/track/click")
async def track_click(
    request: Request,
    lead_id: int = Query(...),
    url: str = Query(...),
    campaign_id: Optional[int] = Query(None),
):
    """
    Track a link click and redirect the user to the destination URL.

    This endpoint:
      1. Decodes + validates the URL
      2. Records the click event for analytics (async, non-blocking)
      3. Redirects the user immediately
    """
    try:
        decoded = unquote(url) if url else None

        if not decoded:
            return JSONResponse({"error": "Missing or invalid URL"}, status_code=400)

        metadata: Dict[str, Any] = {
            "ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "referer": request.headers.get("referer"),
            "url": decoded,
            "ts": _utc_now_iso(),
        }

        resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)

        # Record click for analytics — does not affect follow-up logic
        _record_click(lead_id, resolved_campaign_id, metadata)

        # Always redirect regardless of tracking outcome
        return RedirectResponse(_clean_url(decoded), status_code=302)

    except Exception as e:
        print(f"❌ CLICK ROUTE ERROR: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/click/{lead_id}")
async def track_click_legacy(
    lead_id: int,
    request: Request,
    url: str = Query(...),
    campaign_id: Optional[int] = Query(None),
):
    """Backward-compatible route for older emails pointing to /click/{lead_id}."""
    return await track_click(
        request=request,
        lead_id=lead_id,
        url=url,
        campaign_id=campaign_id,
    )


@router.get("/track/click/test")
async def test_click():
    return JSONResponse({"status": "ok", "message": "click tracker is live"})


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(router)
