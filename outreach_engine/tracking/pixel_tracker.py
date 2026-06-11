# outreach_engine/tracking/pixel_tracker.py

from outreach_engine.database.event_repository import store_event


def handle_pixel_open(lead_id: int, campaign_id: int, email_type: str = "cold", metadata: dict = None):
    """
    Routes open event with email_type preserved in metadata.
    email_type must be 'cold' or 'followup' — caller is responsible for normalizing.
    """
    metadata = metadata or {}
    metadata.setdefault("channel", "email")
    metadata.setdefault("source", "pixel")
    metadata["email_type"] = email_type  # always stamp the type

    return store_event(
        lead_id=lead_id,
        event_type="opened",
        campaign_id=campaign_id,
        metadata=metadata,
    )


def handle_pixel_click(lead_id: int, campaign_id: int, metadata: dict = None):
    metadata = metadata or {}
    metadata.setdefault("channel", "email")
    metadata.setdefault("source", "pixel")

    return store_event(
        lead_id=lead_id,
        event_type="clicked",
        campaign_id=campaign_id,
        metadata=metadata,
    )
