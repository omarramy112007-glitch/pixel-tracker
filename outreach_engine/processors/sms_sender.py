# File: outreach_engine/processors/sms_sender.py

from outreach_engine.tracking.event_repository import store_event
from core.sms_api import sms_api  # SMS API integration placeholder


def send_sms(lead: dict, message: str) -> bool:
    """
    Send SMS to lead and track event.
    """

    success = sms_api.send(lead["phone"], message)

    if success:
        store_event(
            lead_id=lead.get("id"),
            event_type="sent",
            campaign_id=lead.get("campaign_id"),
            metadata={
                "channel": "sms",
                "phone": lead.get("phone")
            }
        )
    else:
        store_event(
            lead_id=lead.get("id"),
            event_type="failed",
            campaign_id=lead.get("campaign_id"),
            metadata={
                "channel": "sms",
                "phone": lead.get("phone"),
                "error": "sms_send_failed"
            }
        )

    return success