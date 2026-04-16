# File: outreach_engine/processors/call_sender.py

from outreach_engine.tracking.event_repository import store_event
from core.call_api import call_api  # Call API


def make_call(lead: dict) -> bool:
    """
    Place a call to the lead and track events.
    """

    result = call_api.place_call(lead["phone"])

    # Normalize result safety
    if result == "answered":
        store_event(
            lead_id=lead["id"],
            campaign_id=lead.get("campaign_id"),
            event_type="answered",
            metadata={"channel": "call"}
        )

    elif result == "made":
        store_event(
            lead_id=lead["id"],
            campaign_id=lead.get("campaign_id"),
            event_type="sent",   # IMPORTANT: "made" should map to sent
            metadata={"channel": "call"}
        )

    else:
        store_event(
            lead_id=lead["id"],
            campaign_id=lead.get("campaign_id"),
            event_type="failed",
            metadata={"channel": "call"}
        )

    return result in ["made", "answered"]