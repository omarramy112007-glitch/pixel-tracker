# outreach_engine/core/engagement_tracker.py

from datetime import datetime
from typing import Any, Dict, Optional


EVENT_SENT      = "sent"
EVENT_FAILED    = "failed"
EVENT_OPENED    = "opened"
EVENT_CLICKED   = "clicked"
EVENT_REPLIED   = "replied"
EVENT_CONVERTED = "converted"

VALID_EVENTS = {
    EVENT_SENT,
    EVENT_FAILED,
    EVENT_OPENED,
    EVENT_CLICKED,
    EVENT_REPLIED,
    EVENT_CONVERTED,
}


def _serialize_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not metadata:
        return {}

    def serialize(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: serialize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [serialize(v) for v in value]
        return value

    return {key: serialize(val) for key, val in metadata.items()}


def normalize_event_type(event: str) -> str:
    event = (event or "").lower().strip()

    aliases = {
        "open":         EVENT_OPENED,
        "opened":       EVENT_OPENED,
        "email_opened": EVENT_OPENED,
        "click":        EVENT_CLICKED,
        "clicked":      EVENT_CLICKED,
        "link_clicked": EVENT_CLICKED,
        "reply":        EVENT_REPLIED,
        "replied":      EVENT_REPLIED,
        "sent":         EVENT_SENT,
        "email_sent":   EVENT_SENT,
        "send":         EVENT_SENT,
        "convert":      EVENT_CONVERTED,
        "converted":    EVENT_CONVERTED,
        "conversion":   EVENT_CONVERTED,
        "deal":         EVENT_CONVERTED,
        "failed":       EVENT_FAILED,
        "failure":      EVENT_FAILED,
    }

    normalized = aliases.get(event, event)

    if normalized not in VALID_EVENTS:
        print(f"⚠️ Unknown event type received: '{event}' → ignoring")
        return "unknown"

    return normalized


def track_event(
    lead:       Dict[str, Any],
    event:      str,
    channel:    str = "email",
    metadata:   Optional[Dict[str, Any]] = None,
    email_type: Optional[str] = None,   # 'cold' | 'followup' | None
) -> None:
    """
    Lightweight console event logger (no DB writes).
    email_type is stamped into metadata so log output is unambiguous.
    """
    if not isinstance(lead, dict):
        print("⚠️ Invalid lead object passed to track_event")
        return

    event = normalize_event_type(event)
    if event == "unknown":
        return

    safe_metadata = _serialize_metadata(metadata)
    if email_type:
        safe_metadata["email_type"] = email_type

    log_payload = {
        "event":      event,
        "lead_email": lead.get("email"),
        "lead_id":    lead.get("id"),
        "channel":    channel,
        "email_type": email_type or "n/a",
        "timestamp":  datetime.utcnow().isoformat(),
        "metadata":   safe_metadata,
    }

    print(f"📊 Event tracked: {log_payload}")
