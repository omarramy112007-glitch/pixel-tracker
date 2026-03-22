# outreach_engine/core/engagement_tracker.py

from typing import Dict, Any

# ---------------------------------------------------
# Event Types
# ---------------------------------------------------

EVENT_SENT = "sent"
EVENT_FAILED = "failed"
EVENT_OPENED = "opened"
EVENT_CLICKED = "clicked"
EVENT_REPLIED = "replied"


# ---------------------------------------------------
# Track Engagement Event
# ---------------------------------------------------

def track_event(lead: Dict[str, Any], event: str):

    if event == EVENT_SENT:
        lead["sent"] = True

    elif event == EVENT_FAILED:
        lead["failed"] = True

    elif event == EVENT_OPENED:
        lead["email_opened"] = True

    elif event == EVENT_CLICKED:
        lead["link_clicked"] = True

    elif event == EVENT_REPLIED:
        lead["replied"] = True

    print(f"📊 Event tracked: {event} for {lead.get('email')}")