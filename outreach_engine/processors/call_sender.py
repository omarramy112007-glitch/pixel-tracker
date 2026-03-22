# File: outreach_engine/processors/call_sender.py

from tracking.engagement_tracking import track_event
from core.call_api import call_api  # Import the Call placeholder API

def make_call(lead: dict) -> bool:
    """
    Place a call to the lead and track events.
    """
    result = call_api.place_call(lead["phone"])
    if result == "answered":
        track_event(lead, "answered", channel="call")
    elif result == "made":
        track_event(lead, "made", channel="call")
    else:
        track_event(lead, "failed", channel="call")

    return result in ["made", "answered"]