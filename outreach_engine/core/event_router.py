# outreach_engine/core/event_router.py

from __future__ import annotations

from typing import Optional, Dict, Any

from outreach_engine.tracking.event_repository import store_event


def _normalize_event_type(event_type: str) -> str:
    et = (event_type or "").lower().strip()

    direct_map = {
        "sent": "sent",
        "opened": "opened",
        "clicked": "clicked",
        "replied": "replied",
        "converted": "converted",
        "failed": "failed",
    }

    if et in direct_map:
        return et

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


def handle_event(
    event_type: str,
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    ✅ SINGLE ENTRY POINT FOR EVENTS

    This function:
    - normalizes event type
    - forwards to event_repository (ONLY source of truth)
    - NO direct DB writes
    - NO CRM updates
    """

    if not lead_id:
        return {"status": "error", "message": "lead_id required"}

    normalized = _normalize_event_type(event_type)

    try:
        result = store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type=normalized,
            metadata=metadata or {},
        )

        return {
            "status": "success",
            "event_type": normalized,
            "result": result,
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}