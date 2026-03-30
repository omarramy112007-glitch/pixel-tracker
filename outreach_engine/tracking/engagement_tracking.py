# File: outreach_engine/tracking/engagement_tracking.py

from datetime import date, datetime
from typing import Optional, Dict, Any

from outreach_engine.core.event_router import handle_event


def _make_json_safe(value: Any) -> Any:
    """
    Convert values that Supabase/JSON can't serialize into safe strings.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _sanitize_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not metadata:
        return {}

    safe = {}
    for key, value in metadata.items():
        safe[key] = _make_json_safe(value)
    return safe


def track_event(
    lead: dict,
    event_type: str,
    channel: str = "email",
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Track any engagement event across multiple channels.

    Args:
        lead: {"id": int, "campaign_id": int}
        event_type: "sent", "opened", "clicked", "replied", "converted",
                    "made", "answered", etc.
        channel: "email", "sms", "linkedin", "call"
        metadata: optional dict (timestamp, IP, user_agent, provider, step, etc)
    """
    if not isinstance(lead, dict):
        print("⚠️ track_event received invalid lead")
        return

    lead_id = lead.get("id")
    campaign_id = lead.get("campaign_id")

    if not lead_id or not campaign_id:
        print("⚠️ Missing lead_id or campaign_id in track_event")
        return

    metadata = _sanitize_metadata(metadata)

    event_name = f"{channel}_{event_type}" if channel != "email" else event_type

    try:
        handle_event(
            event_name,
            campaign_id,
            lead_id=lead_id,
            metadata=metadata,
        )
    except Exception as e:
        print(f"⚠️ track_event failed: {e}")


# --------------------------------------------------
# Email Convenience Functions
# --------------------------------------------------
def track_email_sent(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "sent", "email", metadata)


def track_email_open(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "opened", "email", metadata)


def track_link_click(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "clicked", "email", metadata)


def track_reply(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "replied", "email", metadata)


def track_email_failed(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "failed", "email", metadata)


def track_conversion(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "converted", "email", metadata)


# Backward-compatible aliases
record_email_sent = track_email_sent
record_email_open = track_email_open
record_link_click = track_link_click
record_reply = track_reply
record_email_failed = track_email_failed
record_conversion = track_conversion


# --------------------------------------------------
# SMS Convenience Functions
# --------------------------------------------------
def track_sms_sent(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "sent", "sms", metadata)


def track_sms_clicked(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "clicked", "sms", metadata)


def track_sms_replied(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "replied", "sms", metadata)


# --------------------------------------------------
# LinkedIn Convenience Functions
# --------------------------------------------------
def track_linkedin_message_sent(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "sent", "linkedin", metadata)


def track_linkedin_clicked(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "clicked", "linkedin", metadata)


def track_linkedin_replied(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "replied", "linkedin", metadata)


# --------------------------------------------------
# Call / Phone Convenience Functions
# --------------------------------------------------
def track_call_made(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "made", "call", metadata)


def track_call_answered(lead_id: int, campaign_id: int, metadata: Optional[Dict[str, Any]] = None):
    track_event({"id": lead_id, "campaign_id": campaign_id}, "answered", "call", metadata)