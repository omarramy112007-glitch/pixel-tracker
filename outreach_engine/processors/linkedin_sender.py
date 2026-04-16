# File: outreach_engine/processors/linkedin_sender.py

from outreach_engine.tracking.event_repository import store_event
from core.linkedin_api import linkedin_api  # placeholder API


def send_linkedin_message(lead: dict, message: str) -> bool:
    """
    Send LinkedIn message and track engagement using unified event system.
    """

    success = linkedin_api.send_message(
        lead.get("linkedin_id"),
        message
    )

    if success:
        store_event(
            lead_id=lead.get("id"),
            campaign_id=lead.get("campaign_id"),
            event_type="sent",
            metadata={
                "channel": "linkedin"
            }
        )
    else:
        store_event(
            lead_id=lead.get("id"),
            campaign_id=lead.get("campaign_id"),
            event_type="failed",
            metadata={
                "channel": "linkedin"
            }
        )

    return success