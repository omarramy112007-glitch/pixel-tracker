# outreach_engine/core/event_router.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Dict, Any

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import log_event


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_event_type(event_type: str) -> str:
    et = (event_type or "").lower().strip()

    direct_map = {
        "sent": "sent",
        "opened": "opened",
        "open": "opened",
        "clicked": "clicked",
        "click": "clicked",
        "replied": "replied",
        "reply": "replied",
        "converted": "converted",
        "conversion": "converted",
        "failed": "failed",
    }

    if et in direct_map:
        return direct_map[et]

    suffix_map = {
        "_sent": "sent",
        "_opened": "opened",
        "_clicked": "clicked",
        "_replied": "replied",
        "_converted": "converted",
        "_failed": "failed",
    }

    for suffix, base in suffix_map.items():
        if et.endswith(suffix):
            return base

    return et


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _update_outreach_leads(lead_id: int, event_type: str, now: str) -> None:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        if not res.data:
            return

        row = res.data[0]
        updates: Dict[str, Any] = {"last_updated": now}

        if event_type == "sent":
            updates["status"] = "sent"
            updates["last_email_sent"] = row.get("last_email_sent") or now

        elif event_type == "opened":
            updates["open_count"] = _safe_int(row.get("open_count")) + 1

        elif event_type == "clicked":
            updates["click_count"] = _safe_int(row.get("click_count")) + 1

        elif event_type == "replied":
            updates["reply_count"] = _safe_int(row.get("reply_count")) + 1
            updates["status"] = "replied"

        elif event_type == "converted":
            updates["conversion_count"] = _safe_int(row.get("conversion_count")) + 1
            updates["status"] = "converted"

        elif event_type == "failed":
            updates["status"] = "failed"

        supabase.table("outreach_leads").update(updates).eq("id", lead_id).execute()

    except Exception as e:
        print(f"⚠ outreach_leads update failed: {e}")


def _upsert_crm_metric(lead_id: int, event_type: str, now: str) -> None:
    field_map = {
        "sent": "emails_sent",
        "opened": "opens",
        "clicked": "clicks",
        "replied": "replies",
        "converted": "conversions",
    }

    if event_type not in field_map:
        return

    field = field_map[event_type]

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
                "last_activity": now,
                field: _safe_int(row.get(field)) + 1,
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
            }
            payload[field] = 1
            supabase.table("crm_analytics").insert(payload).execute()

    except Exception as e:
        print(f"⚠ crm_analytics update failed: {e}")


def handle_event(
    event_type: str,
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Single entry point for events:
    - writes to lead_events
    - updates outreach_leads counts/status
    - updates crm_analytics
    """
    if lead_id is None:
        return {"status": "error", "message": "lead_id required"}

    if campaign_id is None:
        return {"status": "error", "message": "campaign_id required"}

    normalized = _normalize_event_type(event_type)
    now = _now_iso()
    metadata = metadata or {}

    try:
        result = log_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type=normalized,
            metadata=metadata,
        )

        _update_outreach_leads(lead_id, normalized, now)
        _upsert_crm_metric(lead_id, normalized, now)

        return {
            "status": "success",
            "event_type": normalized,
            "result": result,
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}