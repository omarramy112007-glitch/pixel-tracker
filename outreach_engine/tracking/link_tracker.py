# File: outreach_engine/tracking/link_tracker.py

from outreach_engine.tracking.engagement_tracking import track_link_click
from urllib.parse import unquote

def handle_link_click(lead_id: int, campaign_id: int, destination_url: str, metadata: dict = None) -> str:
    try:
        decoded_url = unquote(destination_url)
        track_link_click(campaign_id, lead_id=lead_id, metadata=metadata)
        print(f"🔗 Link clicked | Lead {lead_id} | Campaign {campaign_id}")
        return decoded_url
    except Exception as e:
        print(f"⚠ Link tracking failed: {e}")
        return destination_url