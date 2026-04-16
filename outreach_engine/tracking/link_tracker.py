# File: outreach_engine/core/link_tracker.py

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import unquote

from fastapi import FastAPI, Request, Query
from fastapi.responses import RedirectResponse, JSONResponse

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import store_event

app = FastAPI(title="Outreach Engine Link Tracker")


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _record_click(lead_id: int, campaign_id: Optional[int], metadata: Dict[str, Any]) -> None:
    try:
        lead = (
            supabase.table("outreach_leads")
            .select("click_count")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        if not lead.data:
            return

        row = lead.data[0]

        now = datetime.utcnow().isoformat()

        merged_metadata = _json_safe(metadata)
        merged_metadata["clicked_at"] = now

        current_click_count = int(row.get("click_count") or 0)

        # 1) update outreach_leads
        supabase.table("outreach_leads").update({
            "click_count": current_click_count + 1,
            "metadata": merged_metadata,
            "last_updated": now,
        }).eq("id", lead_id).execute()

        # 2) event system (SOURCE OF TRUTH)
        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="click",
            metadata={"channel": "email"},
        )

        # 3) CRM analytics update (MISSING FIX)
        try:
            analytics = (
                supabase.table("crm_analytics")
                .select("clicks")
                .eq("lead_id", lead_id)
                .limit(1)
                .execute()
            )

            if analytics.data:
                current = int(analytics.data[0].get("clicks") or 0)

                supabase.table("crm_analytics").update({
                    "clicks": current + 1
                }).eq("lead_id", lead_id).execute()

        except Exception as e:
            print(f"⚠ CRM analytics update failed: {e}")

        print(f"🔗 Click tracked | Lead {lead_id} | Campaign {campaign_id}")

    except Exception as e:
        print(f"⚠ Click tracking failed: {e}")


def handle_link_click(
    lead_id: int,
    campaign_id: Optional[int],
    destination_url: str,
    metadata: dict = None
) -> str:
    try:
        decoded_url = unquote(destination_url)
        _record_click(lead_id, campaign_id, metadata or {})
        return decoded_url
    except Exception:
        return destination_url


@app.get("/click/{lead_id}")
async def track_click(
    request: Request,
    lead_id: int,
    url: Optional[str] = Query(default=None),
    campaign_id: Optional[int] = Query(default=None),
):
    decoded_url = unquote(url) if url else None

    metadata = {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "url": decoded_url,
        "campaign_id": campaign_id,
        "ts": datetime.utcnow().isoformat(),
    }

    _record_click(lead_id, campaign_id, metadata)

    if decoded_url:
        return RedirectResponse(decoded_url)

    return JSONResponse({"status": "click recorded"})