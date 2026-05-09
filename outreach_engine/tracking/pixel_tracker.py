# outreach_engine/tracking/pixel_tracker.py

from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any, List

from outreach_engine.database.event_repository import get_lead_events, store_event
from outreach_engine.database.supabase_client import supabase


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _base_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = dict(metadata or {})
    data.setdefault("channel", "email")
    data.setdefault("source", "pixel")
    return data


def _already_recorded_today(lead_id: int, campaign_id: int, event_type: str) -> bool:
    """
    Prevent double counting for repeated hits on the same day.
    """
    try:
        events = get_lead_events(lead_id) or []
        today = datetime.utcnow().date().isoformat()

        for event in reversed(events[:200]):
            if (event.get("event_type") or "").lower() != event_type.lower():
                continue

            ev_campaign = event.get("campaign_id")
            if campaign_id is not None and ev_campaign is not None:
                try:
                    if int(ev_campaign) != int(campaign_id):
                        continue
                except Exception:
                    continue

            ts = event.get("timestamp") or event.get("created_at") or ""
            if str(ts)[:10] == today:
                return True

    except Exception as e:
        print(f"⚠ dedupe check failed: {e}")

    return False


def _update_outreach_leads(
    lead_id: int,
    event_type: str,
) -> None:
    """
    Keep outreach_leads in sync so the dashboard can read real counts.
    """
    try:
        res = (
            supabase.table("outreach_leads")
            .select("open_count, click_count, email_opened, link_clicked")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        row = (res.data or [{}])[0]
        now = _utc_now_iso()
        updates: Dict[str, Any] = {"last_updated": now}

        if event_type == "opened":
            updates["open_count"] = _safe_int(row.get("open_count")) + 1
            updates["email_opened"] = True
            updates["email_opened_at"] = now

        elif event_type == "clicked":
            updates["click_count"] = _safe_int(row.get("click_count")) + 1
            updates["link_clicked"] = True
            updates["link_clicked_at"] = now

        supabase.table("outreach_leads").update(updates).eq("id", lead_id).execute()

    except Exception as e:
        print(f"⚠ outreach_leads sync failed: {e}")


def _update_crm_analytics(
    lead_id: int,
    event_type: str,
) -> None:
    """
    Keep crm_analytics in sync too.
    """
    try:
        res = (
            supabase.table("crm_analytics")
            .select("engagement_score, emails_sent, opens, clicks, replies, conversions, last_activity")
            .eq("lead_id", lead_id)
            .limit(1)
            .execute()
        )

        row = (res.data or [{}])[0]
        now = _utc_now_iso()

        payload: Dict[str, Any] = {
            "lead_id": lead_id,
            "last_activity": now,
            "engagement_score": row.get("engagement_score") or 0,
            "emails_sent": _safe_int(row.get("emails_sent")),
            "opens": _safe_int(row.get("opens")),
            "clicks": _safe_int(row.get("clicks")),
            "replies": _safe_int(row.get("replies")),
            "conversions": _safe_int(row.get("conversions")),
        }

        if event_type == "opened":
            payload["opens"] += 1
        elif event_type == "clicked":
            payload["clicks"] += 1

        supabase.table("crm_analytics").upsert(payload).execute()

    except Exception as e:
        print(f"⚠ crm_analytics sync failed: {e}")


def _record_event(
    lead_id: int,
    campaign_id: int,
    event_type: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    payload = _base_metadata(metadata)

    if _already_recorded_today(lead_id, campaign_id, event_type):
        return False

    store_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type=event_type,
        metadata=payload,
    )

    _update_outreach_leads(lead_id, event_type)
    _update_crm_analytics(lead_id, event_type)

    return True


def handle_pixel_open(
    lead_id: int,
    campaign_id: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Record a tracked open once per day per lead/campaign.
    """
    return _record_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="opened",
        metadata=metadata,
    )


def handle_pixel_click(
    lead_id: int,
    campaign_id: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Record a tracked click once per day per lead/campaign.
    """
    return _record_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="clicked",
        metadata=metadata,
    )