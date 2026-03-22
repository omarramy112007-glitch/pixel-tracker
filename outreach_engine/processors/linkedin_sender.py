# File: outreach_engine/processors/linkedin_sender.py

from tracking.engagement_tracking import track_event
from core.linkedin_api import linkedin_api  # Import the LinkedIn placeholder API

def send_linkedin_message(lead: dict, message: str) -> bool:
    """
    Send LinkedIn message and track engagement.
    """
    success = linkedin_api.send_message(lead["linkedin_id"], message)

    if success:
        track_event(lead, "sent", channel="linkedin")
    else:
        track_event(lead, "failed", channel="linkedin")

    return success