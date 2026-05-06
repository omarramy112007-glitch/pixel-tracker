# outreach_engine/database/event_repository.py

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from outreach_engine.database.supabase_client import supabase

CRM_EVENT_FIELD_MAP = {
    "sent": "emails_sent",
    "opened": "opens",
    "clicked": "clicks",
    "replied": "replies",
    "converted": "conversions",
}

CAMPAIGN_EVENT_FIELD_MAP = CRM_EVENT_FIELD_MAP.copy()

EVENT_TYPE_ALIASES = {
    "open": "opened",
    "email_open": "opened",
    "email_opened": "opened",
    "click": "clicked",
    "link_click": "clicked",
    "reply": "replied",
    "response": "replied",
    "conversion": "converted",
    "convert": "converted",
    "email_sent": "sent",
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _normalize_event_type(event_type: str) -> str:
    cleaned = (event_type or "").strip().lower()
    return EVENT_TYPE_ALIASES.get(cleaned, cleaned)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _get_channel(metadata: Dict[str, Any]) -> str:
    return str(metadata.get("channel") or "email").strip().lower()


def _is_email_event(metadata: Dict[str, Any], event_type: str) -> bool:
    channel = _get_channel(metadata)
    return channel in {"email", "gmail"} and _normalize_event_type(event_type) in {
        "sent",
        "opened",
        "clicked",
        "replied",
        "converted",
    }


def _clean_url(url: str) -> str:
    return (url or "").strip().split("?")[0].split("#")[0].rstrip("/").lower()


def _build_event_key(
    lead_id: Any,
    campaign_id: Optional[int],
    event_type: str,
    metadata: Dict[str, Any],
) -> str:
    """
    Kept for observability only.
    Dedupe should not rely on this key anymore.
    """
    normalized_event_type = _normalize_event_type(event_type)

    gmail_message_id = str(metadata.get("gmail_message_id") or "").strip()
    thread_id = str(metadata.get("thread_id") or metadata.get("thread") or "").strip()
    url = _clean_url(str(metadata.get("url") or metadata.get("destination_url") or ""))
    sender = str(metadata.get("sender") or metadata.get("from") or "").strip().lower()
    subject = str(metadata.get("subject") or "").strip().lower()
    timestamp = str(metadata.get("timestamp") or metadata.get("ts") or "").strip()

    if normalized_event_type == "replied":
        anchor = gmail_message_id or timestamp or f"{sender}:{subject}"
    elif normalized_event_type == "clicked":
        anchor = url or timestamp or f"{sender}:{subject}"
    elif normalized_event_type == "opened":
        anchor = timestamp or f"{sender}:{subject}"
    else:
        anchor = gmail_message_id or timestamp or url or f"{sender}:{subject}"

    raw = f"{lead_id}|{campaign_id}|{normalized_event_type}|{anchor}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_exists(
    event_key: str,
    lead_id: Any,
    event_type: str,
    gmail_message_id: Optional[str],
    campaign_id: Optional[int] = None,
) -> bool:
    """
    Deduplicate only on gmail_message_id.
    Do not use event_key for dedupe anymore.
    """
    if not gmail_message_id:
        return False

    try:
        query = (
            supabase.table("lead_events")
            .select("id, metadata, event_type, timestamp")
            .eq("lead_id", lead_id)
            .eq("event_type", event_type)
            .order("timestamp", desc=True)
            .limit(100)
        )

        res = query.execute()
        rows = res.data or []

        for row in rows:
            row_metadata = row.get("metadata") or {}
            if not isinstance(row_metadata, dict):
                continue

            row_gmail_message_id = str(row_metadata.get("gmail_message_id") or "").strip()
            row_campaign_id = row_metadata.get("campaign_id")

            if campaign_id is not None and row_campaign_id is not None:
                try:
                    if int(row_campaign_id) != int(campaign_id):
                        continue
                except Exception:
                    pass

            if row_gmail_message_id and row_gmail_message_id == gmail_message_id:
                return True

        return False

    except Exception:
        return False


def _update_outreach_lead(
    lead_id: Any,
    event_type: str,
    timestamp_iso: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        existing = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        if not existing.data:
            return {"updated": False, "ignored": True, "reason": "lead_not_found"}

        row = existing.data[0]
        status = (row.get("status") or "").lower().strip()
        open_count = _as_int(row.get("open_count", 0))
        click_count = _as_int(row.get("click_count", 0))
        reply_count = _as_int(row.get("reply_count", 0))
        conversion_count = _as_int(row.get("conversion_count", 0))

        optional_fields = {
            key: metadata.get(key)
            for key in ("thread_id", "gmail_message_id")
            if metadata.get(key) not in (None, "")
        }

        if event_type == "sent":
            payload = {
                "status": "sent",
                "last_email_sent": timestamp_iso,
                "last_updated": timestamp_iso,
            }
            if optional_fields:
                payload.update(optional_fields)
            supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
            return {"updated": True, "payload": payload}

        if event_type == "opened":
            payload = {
                "open_count": open_count + 1,
                "email_opened": True,
                "email_opened_at": timestamp_iso,
                "last_updated": timestamp_iso,
            }
            if status in {"pending", "new"}:
                payload["status"] = "sent"
            supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
            return {"updated": True, "payload": payload}

        if event_type == "clicked":
            payload = {
                "click_count": click_count + 1,
                "link_clicked": True,
                "link_clicked_at": timestamp_iso,
                "last_updated": timestamp_iso,
            }
            if status in {"pending", "new"}:
                payload["status"] = "sent"
            supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
            return {"updated": True, "payload": payload}

        if event_type == "replied":
            base_payload = {
                "reply_count": reply_count + 1,
                "status": "replied",
                "reply_status": "replied",
                "replied_at": timestamp_iso,
                "last_contacted": timestamp_iso,
                "last_updated": timestamp_iso,
                "thread_id": metadata.get("thread_id"),
                "gmail_message_id": metadata.get("gmail_message_id"),
                "next_followup": None,
            }

            payload_variants = [
                {**base_payload, **optional_fields},
                base_payload,
            ]

            last_error = None
            for variant in payload_variants:
                try:
                    supabase.table("outreach_leads").update(variant).eq("id", lead_id).execute()
                    return {"updated": True, "payload": variant}
                except Exception as e:
                    last_error = str(e)

            return {"updated": False, "error": last_error}

        if event_type == "converted":
            payload = {
                "conversion_count": conversion_count + 1,
                "status": "converted",
                "deal_closed": True,
                "deal_status": "won",
                "last_updated": timestamp_iso,
            }
            supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
            return {"updated": True, "payload": payload}

        if event_type == "failed":
            payload = {
                "status": "failed",
                "reply_status": "no_reply",
                "last_updated": timestamp_iso,
            }
            supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
            return {"updated": True, "payload": payload}

        return {"updated": False, "ignored": True}

    except Exception as e:
        return {"updated": False, "error": str(e)}


def _update_crm_analytics(
    lead_id: Any,
    event_type: str,
    timestamp_iso: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    if not _is_email_event(metadata, event_type):
        return {"updated": False, "ignored": True}

    field = CRM_EVENT_FIELD_MAP.get(event_type)
    if not field:
        return {"updated": False, "ignored": True}

    try:
        existing = (
            supabase.table("crm_analytics")
            .select("*")
            .eq("lead_id", lead_id)
            .limit(1)
            .execute()
        )

        if existing.data:
            row = existing.data[0]
            payload = {
                "lead_id": lead_id,
                "emails_sent": _as_int(row.get("emails_sent")),
                "opens": _as_int(row.get("opens")),
                "clicks": _as_int(row.get("clicks")),
                "replies": _as_int(row.get("replies")),
                "conversions": _as_int(row.get("conversions")),
                "last_activity": timestamp_iso,
                "engagement_score": _as_int(row.get("engagement_score")),
            }

            payload[field] = _as_int(payload.get(field, 0)) + 1
            payload["engagement_score"] = (
                _as_int(payload["emails_sent"]) * 1
                + _as_int(payload["opens"]) * 2
                + _as_int(payload["clicks"]) * 3
                + _as_int(payload["replies"]) * 5
                + _as_int(payload["conversions"]) * 10
            )

            supabase.table("crm_analytics").update(payload).eq("lead_id", lead_id).execute()
            return {"updated": True, "mode": "update", "field": field}

        payload = {
            "lead_id": lead_id,
            "emails_sent": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
            "last_activity": timestamp_iso,
            "engagement_score": 0,
        }
        payload[field] = 1
        payload["engagement_score"] = (
            _as_int(payload["emails_sent"]) * 1
            + _as_int(payload["opens"]) * 2
            + _as_int(payload["clicks"]) * 3
            + _as_int(payload["replies"]) * 5
            + _as_int(payload["conversions"]) * 10
        )

        supabase.table("crm_analytics").insert(payload).execute()
        return {"updated": True, "mode": "insert", "field": field}

    except Exception as e:
        return {"updated": False, "error": str(e), "field": field}


def _update_campaign_analytics(
    campaign_id: Any,
    event_type: str,
    timestamp_iso: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    if not _is_email_event(metadata, event_type):
        return {"updated": False, "ignored": True}

    field = CAMPAIGN_EVENT_FIELD_MAP.get(event_type)
    if not field:
        return {"updated": False, "ignored": True}

    try:
        today = _utc_now().date().isoformat()
        tomorrow = (_utc_now().date() + timedelta(days=1)).isoformat()

        existing = (
            supabase.table("campaign_analytics")
            .select("*")
            .eq("campaign_id", campaign_id)
            .gte("created_at", today)
            .lt("created_at", tomorrow)
            .limit(1)
            .execute()
        )

        if existing.data:
            row = existing.data[0]
            payload = {
                "campaign_id": campaign_id,
                "emails_sent": _as_int(row.get("emails_sent")),
                "opens": _as_int(row.get("opens")),
                "clicks": _as_int(row.get("clicks")),
                "replies": _as_int(row.get("replies")),
                "conversions": _as_int(row.get("conversions")),
                "emails_per_provider": row.get("emails_per_provider", {}) or {},
                "created_at": today,
            }

            payload[field] = _as_int(payload.get(field, 0)) + 1
            supabase.table("campaign_analytics").update(payload).eq("id", row["id"]).execute()
            return {"updated": True, "mode": "update", "field": field}

        payload = {
            "campaign_id": campaign_id,
            "emails_sent": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
            "emails_per_provider": {},
            "created_at": today,
        }
        payload[field] = 1
        supabase.table("campaign_analytics").insert(payload).execute()
        return {"updated": True, "mode": "insert", "field": field}

    except Exception as e:
        return {"updated": False, "error": str(e), "field": field}


def _insert_lead_event(payload: Dict[str, Any]) -> Any:
    try:
        return supabase.table("lead_events").insert(payload).execute()
    except Exception as e:
        msg = str(e).lower()

        if "campaign_id" in msg or "column" in msg or "schema cache" in msg or "does not exist" in msg:
            fallback_payload = dict(payload)
            fallback_payload.pop("campaign_id", None)
            return supabase.table("lead_events").insert(fallback_payload).execute()

        raise


def log_event(
    lead_id: Any,
    campaign_id: Optional[int],
    event_type: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_event_type = _normalize_event_type(event_type)
    safe_metadata = _json_safe(metadata or {})

    if not isinstance(safe_metadata, dict):
        safe_metadata = {}

    if "channel" not in safe_metadata:
        safe_metadata["channel"] = "email"

    gmail_message_id = str(safe_metadata.get("gmail_message_id") or "").strip()
    event_key = str(safe_metadata.get("event_key") or "").strip() or _build_event_key(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type=normalized_event_type,
        metadata=safe_metadata,
    )

    safe_metadata["event_key"] = event_key
    if campaign_id is not None:
        safe_metadata["campaign_id"] = campaign_id
    if gmail_message_id:
        safe_metadata["gmail_message_id"] = gmail_message_id

    if _event_exists(
        event_key=event_key,
        lead_id=lead_id,
        event_type=normalized_event_type,
        gmail_message_id=gmail_message_id or None,
        campaign_id=campaign_id,
    ):
        return {"status": "duplicate", "event_key": event_key}

    timestamp_iso = _utc_now_iso()

    payload: Dict[str, Any] = {
        "lead_id": lead_id,
        "event_type": normalized_event_type,
        "timestamp": timestamp_iso,
        "metadata": safe_metadata,
    }

    if campaign_id is not None:
        payload["campaign_id"] = campaign_id

    try:
        res = _insert_lead_event(payload)

        outreach_result = _update_outreach_lead(
            lead_id=lead_id,
            event_type=normalized_event_type,
            timestamp_iso=timestamp_iso,
            metadata=safe_metadata,
        )

        crm_result = _update_crm_analytics(
            lead_id=lead_id,
            event_type=normalized_event_type,
            timestamp_iso=timestamp_iso,
            metadata=safe_metadata,
        )

        campaign_result = None
        if campaign_id is not None:
            campaign_result = _update_campaign_analytics(
                campaign_id=campaign_id,
                event_type=normalized_event_type,
                timestamp_iso=timestamp_iso,
                metadata=safe_metadata,
            )

        print(f"✅ Event logged: {normalized_event_type} | Lead {lead_id}")

        return {
            "status": "success",
            "event_key": event_key,
            "data": getattr(res, "data", None),
            "outreach_leads": outreach_result,
            "crm_analytics": crm_result,
            "campaign_analytics": campaign_result,
        }

    except Exception as e:
        print(f"❌ Event logging failed: {e}")
        return {"status": "error", "event_key": event_key, "message": str(e)}


def store_event(
    lead_id: Any,
    event_type: str,
    campaign_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return log_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type=event_type,
        metadata=metadata,
    )


def get_lead_events(lead_id: Any) -> List[Dict[str, Any]]:
    try:
        res = (
            supabase.table("lead_events")
            .select("*")
            .eq("lead_id", lead_id)
            .order("timestamp", desc=True)
            .execute()
        )
        return res.data or []
    except Exception:
        return []


def get_campaign_events(campaign_id: int) -> List[Dict[str, Any]]:
    try:
        try:
            res = (
                supabase.table("lead_events")
                .select("*")
                .eq("campaign_id", campaign_id)
                .order("timestamp", desc=True)
                .execute()
            )
            if res.data is not None:
                return res.data or []
        except Exception:
            pass

        res = supabase.table("lead_events").select("*").order("timestamp", desc=True).execute()
        rows = res.data or []
        filtered = []

        for row in rows:
            md = row.get("metadata") or {}
            if isinstance(md, dict) and md.get("campaign_id") is not None:
                try:
                    if int(md.get("campaign_id")) == int(campaign_id):
                        filtered.append(row)
                except Exception:
                    continue

        return filtered

    except Exception:
        return []


def count_events(campaign_id: int, event_type: Optional[str] = None) -> int:
    events = get_campaign_events(campaign_id)
    if event_type:
        normalized = _normalize_event_type(event_type)
        return sum(1 for e in events if _normalize_event_type(e.get("event_type")) == normalized)
    return len(events)


def get_campaign_metrics(campaign_id: int) -> Dict[str, Any]:
    events = get_campaign_events(campaign_id)

    emails_sent = 0
    opens = 0
    clicks = 0
    replies = 0
    conversions = 0

    for e in events:
        md = e.get("metadata") or {}
        if not isinstance(md, dict):
            md = {}

        et = _normalize_event_type(e.get("event_type"))
        if not _is_email_event(md, et):
            continue

        if et == "sent":
            emails_sent += 1
        elif et == "opened":
            opens += 1
        elif et == "clicked":
            clicks += 1
        elif et == "replied":
            replies += 1
        elif et == "converted":
            conversions += 1

    return {
        "emails_sent": emails_sent,
        "opens": opens,
        "clicks": clicks,
        "replies": replies,
        "conversions": conversions,
        "open_rate": round((opens / emails_sent) * 100, 1) if emails_sent else 0,
        "click_rate": round((clicks / emails_sent) * 100, 1) if emails_sent else 0,
        "reply_rate": round((replies / emails_sent) * 100, 1) if emails_sent else 0,
        "conversion_rate": round((conversions / emails_sent) * 100, 1) if emails_sent else 0,
    }


def get_campaign_funnel(campaign_id: int) -> Dict[str, Any]:
    events = get_campaign_events(campaign_id)

    total_sent = 0
    opened = 0
    clicked = 0
    replied = 0
    converted = 0

    for e in events:
        md = e.get("metadata") or {}
        if not isinstance(md, dict):
            md = {}

        et = _normalize_event_type(e.get("event_type"))
        if not _is_email_event(md, et):
            continue

        if et == "sent":
            total_sent += 1
        elif et == "opened":
            opened += 1
        elif et == "clicked":
            clicked += 1
        elif et == "replied":
            replied += 1
        elif et == "converted":
            converted += 1

    drop_off_reply = ((total_sent - replied) / total_sent * 100) if total_sent else 0
    drop_off_conversion = ((replied - converted) / replied * 100) if replied else 0

    return {
        "sent": total_sent,
        "total_sent": total_sent,
        "opened": opened,
        "clicked": clicked,
        "replied": replied,
        "converted": converted,
        "drop_off_to_reply_pct": round(drop_off_reply, 1),
        "drop_off_to_conversion_pct": round(drop_off_conversion, 1),
    }


def log_ai_action(
    lead: Dict[str, Any],
    action: str,
    priority_score: float,
    reply_probability: float,
    predicted_revenue: float,
):
    return log_event(
        lead_id=lead["id"],
        campaign_id=lead.get("campaign_id"),
        event_type="ai_action",
        metadata={
            "action": action,
            "priority_score": priority_score,
            "reply_probability": reply_probability,
            "predicted_revenue": predicted_revenue,
            "channel": "email",
        },
    )


def log_rl_decision(lead: Dict[str, Any], action: str):
    return log_event(
        lead_id=lead["id"],
        campaign_id=lead.get("campaign_id"),
        event_type="rl_decision",
        metadata={"action": action, "channel": "email"},
    )


def log_conversion(lead_id: Any, campaign_id: int, revenue: float):
    return log_event(
        lead_id,
        campaign_id,
        "converted",
        {"revenue": revenue, "channel": "email"},
    )


def log_email_sent(lead_id: Any, campaign_id: int):
    return log_event(lead_id, campaign_id, "sent", {"channel": "email"})


def log_email_opened(lead_id: Any, campaign_id: int):
    return log_event(lead_id, campaign_id, "opened", {"channel": "email"})


def log_link_clicked(lead_id: Any, campaign_id: int):
    return log_event(lead_id, campaign_id, "clicked", {"channel": "email"})


def log_reply(lead_id: Any, campaign_id: int):
    return log_event(lead_id, campaign_id, "replied", {"channel": "email"})


def delete_old_events(days: int = 90):
    try:
        cutoff_iso = (_utc_now() - timedelta(days=days)).isoformat()
        res = (
            supabase.table("lead_events")
            .delete()
            .lt("timestamp", cutoff_iso)
            .execute()
        )
        return {"deleted": True, "data": res.data}
    except Exception as e:
        return {"deleted": False, "error": str(e)}