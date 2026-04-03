# outreach_engine/tracking/engagement_tracking.py

from datetime import date, datetime
from typing import Optional, Dict, Any

from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase


def _make_json_safe(value: Any) -> Any:
    """
    Convert values that Supabase/JSON can't serialize into safe strings.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _make_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_make_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_make_json_safe(v) for v in value]
    return value


def _sanitize_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not metadata:
        return {}
    return _make_json_safe(metadata)


def _update_outreach_leads(
    lead_id: int,
    event_type: str,
    metadata: Dict[str, Any],
) -> None:
    """
    Keeps outreach_leads in sync with sent/opened/clicked/replied/conversion events.
    """
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .execute()
        )

        if not res.data:
            return

        row = res.data[0]
        now = datetime.utcnow().isoformat()
        updates: Dict[str, Any] = {"last_updated": now}

        current_status = (row.get("status") or "").lower().strip()

        if event_type == "sent":
            updates["status"] = "sent"
            updates["last_email_sent"] = metadata.get("timestamp") or now
            if metadata.get("step") is not None:
                updates["followup_step"] = int(metadata["step"])

        elif event_type == "opened":
            updates["open_count"] = int(row.get("open_count", 0) or 0) + 1

        elif event_type == "clicked":
            updates["click_count"] = int(row.get("click_count", 0) or 0) + 1

        elif event_type == "replied":
            updates["reply_count"] = int(row.get("reply_count", 0) or 0) + 1
            updates["status"] = "replied"

        elif event_type == "converted":
            updates["conversion_count"] = int(row.get("conversion_count", 0) or 0) + 1
            updates["status"] = "converted"

        elif event_type == "failed":
            updates["status"] = "failed"

        else:
            # keep last_updated only for non-email / unsupported events
            pass

        if metadata:
            existing_metadata = row.get("metadata") or {}
            if not isinstance(existing_metadata, dict):
                existing_metadata = {}
            updates["metadata"] = {**existing_metadata, **metadata}

        # Avoid overwriting a later status with an older one.
        if current_status in {"replied", "converted", "failed"} and event_type == "sent":
            updates.pop("status", None)
            updates.pop("last_email_sent", None)

        supabase.table("outreach_leads").update(updates).eq("id", lead_id).execute()

    except Exception as e:
        print(f"⚠ outreach_leads tracking update failed: {e}")


def _update_crm_analytics(
    lead_id: int,
    event_type: str,
    metadata: Dict[str, Any],
) -> None:
    """
    Keeps crm_analytics in sync with counts and last activity.
    """
    try:
        res = (
            supabase.table("crm_analytics")
            .select("*")
            .eq("lead_id", lead_id)
            .execute()
        )

        now = datetime.utcnow().isoformat()

        count_fields = {
            "sent": "emails_sent",
            "opened": "opens",
            "clicked": "clicks",
            "replied": "replies",
            "converted": "conversions",
        }

        increment_field = count_fields.get(event_type)

        if res.data and len(res.data) > 0:
            row = res.data[0]
            payload: Dict[str, Any] = {
                "lead_id": lead_id,
                "last_activity": now,
            }

            if increment_field:
                payload[increment_field] = int(row.get(increment_field, 0) or 0) + 1

            if metadata and "engagement_score" in metadata:
                payload["engagement_score"] = metadata["engagement_score"]

            supabase.table("crm_analytics").update(payload).eq("lead_id", lead_id).execute()
        else:
            payload = {
                "lead_id": lead_id,
                "engagement_score": metadata.get("engagement_score", 0),
                "emails_sent": 0,
                "opens": 0,
                "clicks": 0,
                "replies": 0,
                "conversions": 0,
                "last_activity": now,
            }

            if increment_field:
                payload[increment_field] = 1

            supabase.table("crm_analytics").insert(payload).execute()

    except Exception as e:
        print(f"⚠ crm_analytics tracking update failed: {e}")


def track_event(
    lead: dict,
    event_type: str,
    channel: str = "email",
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Track any engagement event across multiple channels.
    """
    if not isinstance(lead, dict):
        print("⚠️ track_event received invalid lead")
        return

    lead_id = lead.get("id")
    campaign_id = lead.get("campaign_id")

    if not lead_id or not campaign_id:
        print("⚠️ Missing lead_id or campaign_id in track_event")
        return

    safe_metadata = _sanitize_metadata(metadata)
    safe_metadata["channel"] = channel

    event_type = (event_type or "").lower().strip()

    try:
        # 1) raw event log
        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type=event_type,
            metadata=safe_metadata,
        )
    except Exception as e:
        print(f"⚠️ store_event failed: {e}")

    try:
        # 2) outreach_leads table sync
        _update_outreach_leads(lead_id, event_type, safe_metadata)
    except Exception as e:
        print(f"⚠️ outreach_leads sync failed: {e}")

    try:
        # 3) crm_analytics sync
        _update_crm_analytics(lead_id, event_type, safe_metadata)
    except Exception as e:
        print(f"⚠️ crm_analytics sync failed: {e}")


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