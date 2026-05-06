from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any

from outreach_engine.core.event_router import handle_event
from outreach_engine.database.event_repository import get_events_for_lead, store_event


def _base_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = dict(metadata or {})
    data.setdefault("channel", "email")
    data.setdefault("source", "pixel")
    return data


def _already_recorded_today(lead_id: int, campaign_id: int, event_type: str) -> bool:
    """
    Simple dedupe so manual URL hits do not inflate counts.
    """
    try:
        events = get_events_for_lead(lead_id) or []
        today = datetime.utcnow().date().isoformat()

        for event in reversed(events[-200:]):
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

    except Exception:
        pass

    return False


def handle_pixel_open(
    lead_id: int,
    campaign_id: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Record a tracked open once per day per lead/campaign.
    """
    payload = _base_metadata(metadata)

    if _already_recorded_today(lead_id, campaign_id, "opened"):
        return False

    store_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="opened",
        metadata=payload,
    )

    handle_event(
        event_type="opened",
        campaign_id=campaign_id,
        lead_id=lead_id,
        metadata=payload,
    )

    return True


def handle_pixel_click(
    lead_id: int,
    campaign_id: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Record a tracked click once per day per lead/campaign/url hash handled elsewhere.
    """
    payload = _base_metadata(metadata)

    store_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="clicked",
        metadata=payload,
    )

    handle_event(
        event_type="clicked",
        campaign_id=campaign_id,
        lead_id=lead_id,
        metadata=payload,
    )

    return True