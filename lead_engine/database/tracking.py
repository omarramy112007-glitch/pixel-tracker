# lead_engine/database/tracking.py

from datetime import datetime
from typing import Any, Dict, Optional

from lead_engine.database.supabase_client import supabase


EVENT_TO_LEADS_UPDATES = {
    "sent": {
        "outreach_status": "Contacted",
    },
    "email_sent": {
        "outreach_status": "Contacted",
    },
    "opened": {
        "email_opened": True,
    },
    "open": {
        "email_opened": True,
    },
    "email_opened": {
        "email_opened": True,
    },
    "clicked": {
        "link_clicked": True,
    },
    "click": {
        "link_clicked": True,
    },
    "link_clicked": {
        "link_clicked": True,
    },
    "replied": {
        "reply_status": "Replied",
    },
    "reply": {
        "reply_status": "Replied",
    },
    "meeting": {
        "meeting_booked": True,
        "pipeline_stage": "Proposal",
    },
    "converted": {
        "deal_closed": True,
        "deal_status": "Won",
        "pipeline_stage": "Closed",
    },
    "deal": {
        "deal_closed": True,
        "deal_status": "Won",
        "pipeline_stage": "Closed",
    },
    "failed": {
        "deal_status": "Lost",
    },
}

EVENT_TO_CRM_FIELD = {
    "sent": "emails_sent",
    "email_sent": "emails_sent",
    "opened": "opens",
    "open": "opens",
    "email_opened": "opens",
    "clicked": "clicks",
    "click": "clicks",
    "link_clicked": "clicks",
    "replied": "replies",
    "reply": "replies",
    "meeting": "conversions",
    "converted": "conversions",
    "deal": "conversions",
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    return value


def _update_leads_table(lead_id: str, event_type: str, metadata: Optional[Dict[str, Any]]) -> None:
    event_key = (event_type or "").lower().strip()
    updates = dict(EVENT_TO_LEADS_UPDATES.get(event_key, {}))

    if metadata:
        updates["metadata"] = _json_safe(metadata)

    if not updates:
        return

    updates["updated_at"] = datetime.utcnow().isoformat()

    if event_key in {"sent", "email_sent"}:
        updates["email_sent_at"] = datetime.utcnow().isoformat()
    elif event_key in {"opened", "open", "email_opened"}:
        updates["email_opened_at"] = datetime.utcnow().isoformat()
        updates["open_count"] = 1
    elif event_key in {"clicked", "click", "link_clicked"}:
        updates["link_clicked_at"] = datetime.utcnow().isoformat()
    elif event_key in {"replied", "reply"}:
        updates["reply_count"] = 1
        updates["last_contacted"] = datetime.utcnow().isoformat()
    elif event_key in {"meeting"}:
        updates["meeting_count"] = 1
    elif event_key in {"converted", "deal"}:
        updates["deal_closed"] = True

    try:
        row = (
            supabase.table("leads")
            .select("*")
            .eq("id", lead_id)
            .execute()
        ).data

        if not row:
            return

        current = row[0]

        # increment counters safely
        if event_key in {"opened", "open", "email_opened"}:
            updates["open_count"] = int(current.get("open_count", 0) or 0) + 1
            updates["email_opened"] = True

        if event_key in {"clicked", "click", "link_clicked"}:
            updates["link_clicked"] = True

        if event_key in {"replied", "reply"}:
            updates["reply_count"] = int(current.get("reply_count", 0) or 0) + 1
            updates["reply_status"] = "Replied"

        if event_key in {"meeting"}:
            updates["meeting_count"] = int(current.get("meeting_count", 0) or 0) + 1
            updates["meeting_booked"] = True

        if event_key in {"converted", "deal"}:
            updates["deal_closed"] = True
            updates["deal_status"] = "Won"
            updates["pipeline_stage"] = "Closed"

        supabase.table("leads").update(updates).eq("id", lead_id).execute()

    except Exception as e:
        print(f"❌ Lead table tracking error: {e}")


def _update_crm_analytics(lead_id: str, event_type: str) -> None:
    event_key = (event_type or "").lower().strip()
    field = EVENT_TO_CRM_FIELD.get(event_key)

    if not field:
        return

    try:
        existing = (
            supabase.table("crm_analytics")
            .select("*")
            .eq("lead_id", lead_id)
            .execute()
        )

        now = datetime.utcnow().isoformat()

        if existing.data and len(existing.data) > 0:
            row = existing.data[0]
            payload = {
                "lead_id": lead_id,
                "last_activity": now,
                field: int(row.get(field, 0) or 0) + 1,
            }
            supabase.table("crm_analytics").update(payload).eq("lead_id", lead_id).execute()
        else:
            payload = {
                "lead_id": lead_id,
                "engagement_score": 0,
                "emails_sent": 0,
                "opens": 0,
                "clicks": 0,
                "replies": 0,
                "conversions": 0,
                "last_activity": now,
                field: 1,
            }
            supabase.table("crm_analytics").insert(payload).execute()

    except Exception as e:
        print(f"❌ CRM analytics tracking error: {e}")


def track_event(
    lead_id: str,
    event_type: str,
    metadata: Optional[Dict[str, Any]] = None,
    campaign_id: Optional[int] = None,
):
    """
    Tracks an event by:
    1) inserting into lead_events
    2) updating leads table counters/flags
    3) updating crm_analytics
    """
    try:
        payload = {
            "lead_id": lead_id,
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": _json_safe(metadata or {}),
        }

        # Optional if your schema supports it; if not, still works with lead_events table.
        if campaign_id is not None:
            payload["campaign_id"] = campaign_id

        try:
            supabase.table("lead_events").insert(payload).execute()
        except Exception:
            # fallback without campaign_id in case the table schema is strict
            payload.pop("campaign_id", None)
            supabase.table("lead_events").insert(payload).execute()

        _update_leads_table(lead_id, event_type, metadata)
        _update_crm_analytics(lead_id, event_type)

        print(f"📊 Tracked {event_type} for {lead_id}")

    except Exception as e:
        print(f"❌ Tracking error: {e}")