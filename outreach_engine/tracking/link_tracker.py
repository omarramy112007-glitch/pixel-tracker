# outreach_engine/tracking/link_tracker.py

from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any
from urllib.parse import unquote

from fastapi import APIRouter, Request, Query
from fastapi.responses import RedirectResponse, JSONResponse

from outreach_engine.database.supabase_client import supabase
from outreach_engine.database.event_repository import store_event

router = APIRouter()


def _resolve_campaign_id(lead_id: int) -> Optional[int]:
    """
    Resolve campaign_id from outreach_leads if not explicitly provided.
    """
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


def _record_click(lead_id: int, campaign_id: Optional[int], metadata: Dict[str, Any]):
    now = datetime.utcnow().isoformat()

    try:
        lead_res = (
            supabase.table("outreach_leads")
            .select("click_count")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        if not lead_res.data:
            print(f"⚠ No outreach_leads row found for lead_id={lead_id}")
            return

        clicks = int(lead_res.data[0].get("click_count") or 0)

        supabase.table("outreach_leads").update({
            "click_count": clicks + 1,
            "last_updated": now,
        }).eq("id", lead_id).execute()

        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="clicked",
            metadata={**metadata, "channel": "email"},
        )

        crm_res = (
            supabase.table("crm_analytics")
            .select("clicks")
            .eq("lead_id", lead_id)
            .limit(1)
            .execute()
        )

        if crm_res.data:
            current_clicks = int(crm_res.data[0].get("clicks") or 0)
            supabase.table("crm_analytics").update({
                "clicks": current_clicks + 1,
                "last_activity": now,
            }).eq("lead_id", lead_id).execute()
        else:
            supabase.table("crm_analytics").insert({
                "lead_id": lead_id,
                "campaign_id": campaign_id,
                "engagement_score": 0,
                "emails_sent": 0,
                "opens": 0,
                "clicks": 1,
                "replies": 0,
                "conversions": 0,
                "last_activity": now,
            }).execute()

        print(f"🖱 Click tracked | Lead {lead_id} | Campaign {campaign_id}")

    except Exception as e:
        print("click tracking error:", e)


@router.get("/track/click")
async def track_click(
    request: Request,
    lead_id: int = Query(...),
    url: str = Query(...),
    campaign_id: Optional[int] = Query(None),
):
    decoded = unquote(url) if url else None

    metadata = {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "referer": request.headers.get("referer"),
        "url": decoded,
        "ts": datetime.utcnow().isoformat(),
    }

    resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)
    _record_click(lead_id, resolved_campaign_id, metadata)

    return RedirectResponse(decoded)


@router.get("/click/{lead_id}")
async def track_click_path(
    lead_id: int,
    request: Request,
    url: str = Query(...),
    campaign_id: Optional[int] = Query(None),
):
    decoded = unquote(url) if url else None

    metadata = {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "referer": request.headers.get("referer"),
        "url": decoded,
        "ts": datetime.utcnow().isoformat(),
    }

    resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)
    _record_click(lead_id, resolved_campaign_id, metadata)

    return RedirectResponse(decoded)


@router.get("/track/click/test")
async def test_click():
    return JSONResponse({"status": "ok", "message": "click tracker is live"})