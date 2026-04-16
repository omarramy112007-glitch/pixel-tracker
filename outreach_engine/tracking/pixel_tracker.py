# outreach_engine/tracking/pixel_tracker.py

from outreach_engine.tracking.event_repository import store_event


def handle_pixel_open(lead_id, campaign_id, metadata):
    metadata = metadata or {}
    metadata.setdefault("channel", "email")
    metadata.setdefault("source", "pixel")

    return store_event(
        lead_id=lead_id,
        event_type="opened",
        campaign_id=campaign_id,
        metadata=metadata
    )