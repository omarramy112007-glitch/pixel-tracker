# outreach_engine/tracking/pixel_tracker.py

from typing import Optional, Dict, Any

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.engagement_tracking import track_email_open


def _resolve_campaign_id(lead_id: int) -> Optional[int]:
    """
    Try to resolve the campaign_id from outreach_leads using lead_id.
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


def handle_pixel_open(
    lead_id: int,
    campaign_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Record an email open event from the tracking pixel.
    If campaign_id is missing, try to resolve it from outreach_leads.
    """
    try:
        resolved_campaign_id = campaign_id or _resolve_campaign_id(lead_id)

        if not resolved_campaign_id:
            print(f"⚠ Cannot track open: missing campaign_id for lead {lead_id}")
            return

        track_email_open(
            lead_id=lead_id,
            campaign_id=resolved_campaign_id,
            metadata=metadata
        )
        print(f"📬 Email opened | Lead {lead_id} | Campaign {resolved_campaign_id}")

    except Exception as e:
        print(f"⚠ Pixel tracking failed: {e}")