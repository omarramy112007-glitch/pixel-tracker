# outreach_engine/core/engagement_tracker.py

from typing import Dict, Any, Optional
from datetime import datetime

# --------------------------------------------------
# Event Types
# --------------------------------------------------

EVENT_SENT = "sent"
EVENT_FAILED = "failed"
EVENT_OPENED = "opened"
EVENT_CLICKED = "clicked"
EVENT_REPLIED = "replied"
EVENT_CONVERTED = "converted"


def _serialize_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Make metadata safe to store/log.
    Converts datetime objects to ISO strings.
    """
    if not metadata:
        return {}

    safe = {}
    for key, value in metadata.items():
        if isinstance(value, datetime):
            safe[key] = value.isoformat()
        elif isinstance(value, dict):
            safe[key] = _serialize_metadata(value)
        elif isinstance(value, list):
            safe[key] = [
                item.isoformat() if isinstance(item, datetime) else item
                for item in value
            ]
        else:
            safe[key] = value
    return safe


# --------------------------------------------------
# Track Engagement Event
# --------------------------------------------------

def track_event(
    lead: Dict[str, Any],
    event: str,
    channel: str = "email",
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Track engagement on a lead dict locally.

    This updates flags on the lead object itself.
    """
    if not isinstance(lead, dict):
        print("⚠️ Invalid lead object passed to track_event")
        return

    metadata = _serialize_metadata(metadata)

    if event == EVENT_SENT:
        lead["sent"] = True
        lead["status"] = "sent"

    elif event == EVENT_FAILED:
        lead["failed"] = True
        lead["status"] = "failed"

    elif event == EVENT_OPENED:
        lead["email_opened"] = True

    elif event == EVENT_CLICKED:
        lead["link_clicked"] = True

    elif event == EVENT_REPLIED:
        lead["replied"] = True
        lead["reply_status"] = "replied"

    elif event == EVENT_CONVERTED:
        lead["converted"] = True
        lead["deal_status"] = "won"

    lead["last_event"] = event
    lead["last_event_channel"] = channel
    lead["last_event_metadata"] = metadata

    print(f"📊 Event tracked: {event} for {lead.get('email')} | channel={channel}")