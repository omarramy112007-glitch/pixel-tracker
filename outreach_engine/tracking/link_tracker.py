# outreach_engine/tracking/link_tracker.py

from typing import Optional
from urllib.parse import unquote

from outreach_engine.tracking.engagement_tracking import track_link_click


def handle_link_click(
    lead_id: int,
    campaign_id: int,
    destination_url: str,
    metadata: Optional[dict] = None
) -> str:
    """
    Decode the destination URL, track the click, and return the clean URL.
    """
    try:
        decoded_url = unquote(destination_url)

        track_link_click(
            lead_id=lead_id,
            campaign_id=campaign_id,
            metadata=metadata
        )

        print(f"🔗 Link clicked | Lead {lead_id} | Campaign {campaign_id}")
        return decoded_url

    except Exception as e:
        print(f"⚠ Link tracking failed: {e}")
        return destination_url