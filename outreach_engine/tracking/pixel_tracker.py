# outreach_engine/tracking/pixel_tracker.py

from outreach_engine.database.event_repository import store_event


def handle_pixel_open(lead_id, campaign_id, metadata=None):
    metadata = metadata or {}
    metadata.setdefault("channel", "email")
    metadata.setdefault("source", "pixel")

    return store_event(
        lead_id=lead_id,
        event_type="opened",
        campaign_id=campaign_id,
        metadata=metadata
    )


def handle_pixel_click(lead_id, campaign_id, metadata=None):
    metadata = metadata or {}
    metadata.setdefault("channel", "email")
    metadata.setdefault("source", "pixel")

    return store_event(
        lead_id=lead_id,
        event_type="clicked",
        campaign_id=campaign_id,
        metadata=metadata
    )