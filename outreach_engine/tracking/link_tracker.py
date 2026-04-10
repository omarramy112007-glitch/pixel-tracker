# outreach_engine/core/link_tracker.py

from typing import Optional
from urllib.parse import unquote

from fastapi import FastAPI, Request, Query
from fastapi.responses import RedirectResponse, JSONResponse

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.engagement_tracking import track_link_click

app = FastAPI(title="Outreach Engine Link Tracker")


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
        print(f"⚠ Failed to resolve campaign_id for lead {lead_id}: {e}")
    return None


@app.get("/click/{lead_id}")
@app.get("/track-click")
async def track_click(
    request: Request,
    lead_id: Optional[int] = None,
    url: Optional[str] = Query(default=None),
    campaign_id: Optional[int] = Query(default=None),
):
    """
    Track link clicks and optionally redirect to destination URL.

    Supports:
      /click/{lead_id}?url=...&campaign_id=...
      /track-click?lead_id=...&url=...&campaign_id=...
    """
    if lead_id is None:
        # support /track-click?lead_id=123
        try:
            lead_id = int(request.query_params.get("lead_id"))
        except Exception:
            return JSONResponse({"status": "error", "message": "lead_id is required"}, status_code=400)

    decoded_url = unquote(url) if url else None

    resolved_campaign_id = campaign_id or _resolve_campaign_id(int(lead_id))

    metadata = {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "url": decoded_url,
        "campaign_id": resolved_campaign_id,
        "referer": request.headers.get("referer"),
    }

    if resolved_campaign_id:
        try:
            track_link_click(
                lead_id=int(lead_id),
                campaign_id=int(resolved_campaign_id),
                metadata=metadata,
            )
            print(f"🔗 Link clicked | Lead {lead_id} | Campaign {resolved_campaign_id}")
        except Exception as e:
            print(f"⚠ Click tracking failed: {e}")
    else:
        print(f"⚠ Click received but campaign_id could not be resolved for lead {lead_id}")

    if decoded_url:
        return RedirectResponse(decoded_url)

    return JSONResponse(
        {
            "status": "click recorded",
            "lead_id": lead_id,
            "campaign_id": resolved_campaign_id,
        }
    )


@app.get("/health")
def health():
    return {"status": "ok"}