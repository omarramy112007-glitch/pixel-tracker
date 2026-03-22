# File: outreach_engine/tracking/pixel_tracker.py

from outreach_engine.tracking.engagement_tracking import track_email_open

def handle_pixel_open(lead_id: int, campaign_id: int, metadata: dict = None) -> None:
    try:
        track_email_open(campaign_id, lead_id=lead_id, metadata=metadata)
        print(f"📬 Email opened | Lead {lead_id} | Campaign {campaign_id}")
    except Exception as e:
        print(f"⚠ Pixel tracking failed: {e}")