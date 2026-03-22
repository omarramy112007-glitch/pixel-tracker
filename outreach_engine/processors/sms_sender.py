# File: outreach_engine/processors/sms_sender.py

from tracking.engagement_tracking import track_event
from core.sms_api import sms_api  # Import the SMS placeholder API

def send_sms(lead: dict, message: str) -> bool:
    """
    Send SMS to lead and track event.
    """
    success = sms_api.send(lead["phone"], message)  # your SMS API integration

    if success:
        # Track multi-channel event
        track_event(lead, event_type="sent", channel="sms")
    else:
        track_event(lead, event_type="failed", channel="sms")

    return success