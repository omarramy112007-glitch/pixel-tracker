# outreach_engine/tracking/link_tracker.py

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Optional, Dict, Any
from urllib.parse import unquote

from fastapi import APIRouter, Request, Query, FastAPI
from fastapi.responses import RedirectResponse, JSONResponse

from outreach_engine.database.supabase_client import supabase
from outreach_engine.database.event_repository import store_event, get_events_for_lead

router = APIRouter()

CLICK_CACHE: dict[str, float] = {}
CLICK_DEDUP_SECONDS = 30


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
        print(f"⚠ Failed to resolve campaign_id for click tracking: {e}")
    return None


def _normalize_url(url: str) -> str:
    return (url or "").strip()


def _clean_url(url: str) -> str:
    return _normalize_url(url).split("?")[0].split("#")[0].rstrip("/")


def _url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _build_click_key(lead_id: int, url: str, day_str: Optional[str] = None) -> str:
    if not day_str:
        day_str = datetime.utcnow().date().isoformat()
    return f"{lead_id}:click:{day_str}:{_url_hash(_clean_url(url))}"


def _should_ignore_click(lead_id: int, url: str) -> bool:
    now = time.time()
    key = _build_click_key(lead_id, url)

    last_seen = CLICK_CACHE.get(key)
    if last_seen and (now - last_seen) < CLICK_DEDUP_SECONDS:
        return True

    CLICK_CACHE[key] = now
    return False


def _click_already_recorded(lead_id: int, url: str) -> bool:
    url_hash = _url_hash(_clean_url(url))
    try:
        events = get_events_for_lead(lead_id) or []
        for event in events[-20:]:
            if event.get("event_type") != "clicked":
                continue
            metadata = event.get("metadata") or {}
            if metadata.get("click_hash") == url_hash:
                return True
    except Exception as e:
        print(f"⚠ click dedupe check failed: {e}")
    return False


def _record_click(
    lead_id: int,
    campaign_id: Optional[int],
    metadata: Dict[str, Any]
):
    try:
        url = _normalize_url(metadata.get("url") or "")
        if not url:
            print("⚠ click ignored: missing url")
            return

        clean_url = _clean_url(url)

        if _should_ignore_click(lead_id, clean_url):
            print(f"🧠 Duplicate click ignored (cooldown) → Lead {lead_id}")
            return

        if _click_already_recorded(lead_id, clean_url):
            print(f"🧠 Duplicate click ignored (DB) → Lead {lead_id}")
            return

        click_date = datetime.utcnow().date().isoformat()
        click_hash = _url_hash(clean_url)

        event_metadata = {
            **metadata,
            "url": clean_url,
            "click_date": click_date,
            "click_hash": click_hash,
            "channel": "email",
        }

        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="clicked",
            metadata=event_metadata,
        )

        print(f"🖱 Click tracked | Lead {lead_id} | Campaign {campaign_id}")

    except Exception as e:
        print("❌ click tracking error:", e)


@router.get("/track/click")
async def track_click(
    request: Request,
    lead_id: int = Query(...),
    url: str = Query(...),
    campaign_id: Optional[int] = Query(None),
):
    try:
        decoded = unquote(url) if url else None

        if not decoded:
            return JSONResponse({"error": "Missing or invalid URL"}, status_code=400)

        metadata = {
            "ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "referer": request.headers.get("referer"),
            "url": decoded,
            "ts": datetime.utcnow().isoformat(),
        }

        resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)
        _record_click(lead_id, resolved_campaign_id, metadata)

        return RedirectResponse(_clean_url(decoded), status_code=302)

    except Exception as e:
        print("❌ CLICK ROUTE ERROR:", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/track/click/test")
async def test_click():
    return JSONResponse({
        "status": "ok",
        "message": "click tracker is live"
    })


app = FastAPI()
app.include_router(router)