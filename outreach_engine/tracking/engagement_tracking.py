# outreach_engine/tracking/engagement_tracking.py

from datetime import datetime
from typing import Any, Dict, Optional

from outreach_engine.tracking.event_repository import log_event


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
    if not isinstance(lead, dict):
        print("⚠️ Invalid lead object passed to track_event")
        return

    lead_id = lead.get("id")
    if not lead_id:
        print("⚠️ Missing lead_id in track_event")
        return

    campaign_id = lead.get("campaign_id")
    event       = normalize_event_type(event)

    if event == "unknown":
        return

    safe_metadata             = _serialize_metadata(metadata)
    safe_metadata["channel"]  = channel
    safe_metadata["lead_email"] = lead.get("email")

    # Stamp email_type so downstream writers can route to the right counter
    if email_type:
        safe_metadata["email_type"] = email_type

    result = log_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type=event,
        metadata=safe_metadata,
    )

    print(f"📊 Event tracked: {result.get('status', 'unknown')} | {event} | lead_id={lead_id} | type={email_type or 'n/a'}")


# ── Convenience wrappers ────────────────────────────────────────────────────

def track_email_sent(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "sent", "email", metadata)


def track_email_open(
    lead_id:    int,
    campaign_id: int,
    email_type: str = "cold",           # caller must pass 'cold' or 'followup'
    metadata:   Optional[Dict[str, Any]] = None,
):
    track_event(
        {"id": lead_id, "campaign_id": campaign_id},
        "opened",
        "email",
        metadata,
        email_type=email_type,
    )


def track_link_click(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "clicked", "email", metadata)


def track_reply(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "replied", "email", metadata)


def track_email_failed(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "failed", "email", metadata)


def track_conversion(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "converted", "email", metadata)


def track_sms_sent(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "sent", "sms", metadata)


def track_sms_clicked(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "clicked", "sms", metadata)


def track_sms_replied(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "replied", "sms", metadata)


def track_linkedin_message_sent(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "sent", "linkedin", metadata)


def track_linkedin_clicked(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "clicked", "linkedin", metadata)


def track_linkedin_replied(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "replied", "linkedin", metadata)


def track_call_made(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "sent", "call", metadata)


def track_call_answered(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "replied", "call", metadata)
