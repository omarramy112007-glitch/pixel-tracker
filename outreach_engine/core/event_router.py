# outreach_engine/core/event_router.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import log_event


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Terminal states: once a lead reaches one of these, stop all automation.
STOP_EVENTS = {"converted", "failed", "opt_out", "completed"}

# Analytics-only events: we track them, but they do not affect routing.
ANALYTICS_ONLY_EVENTS = {"clicked"}

# Event normalization map
_EVENT_ALIASES = {
    "open": "opened",
    "opened": "opened",
    "click": "clicked",
    "clicked": "clicked",
    "reply": "replied",
    "replied": "replied",
    "sent": "sent",
    "converted": "converted",
    "conversion": "converted",
    "failed": "failed",
    "optout": "opt_out",
    "opt_out": "opt_out",
    "unsubscribe": "opt_out",
    "unsubscribed": "opt_out",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_naive_iso() -> str:
    return datetime.utcnow().isoformat()


def _normalize_event_type(event_type: str) -> str:
    raw = (event_type or "").strip().lower()
    return _EVENT_ALIASES.get(raw, raw)


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _fetch_outreach_lead(lead_id: Any) -> Optional[Dict[str, Any]]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    except Exception:
        pass
    return None


def _update_outreach_lead(lead_id: Any, payload: Dict[str, Any]) -> None:
    try:
        supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def handle_event(
    event_type: str,
    campaign_id: int,
    lead_id: Optional[Any] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Central event router.

    Rules:
      - clicked  -> analytics only (no decision layer)
      - opened   -> increment open_count, set email_opened flags
      - replied  -> increment reply_count, set reply flags
      - sent     -> mark as sent (initial state or in-flight send)
      - converted / failed / opt_out -> terminal
    """
    if lead_id is None:
        return {"status": "error", "message": "lead_id is required"}
    if campaign_id is None:
        return {"status": "error", "message": "campaign_id is required"}

    normalized = _normalize_event_type(event_type)
    metadata = _safe_dict(metadata)

    # Always log the event first.
    try:
        log_result = log_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type=normalized,
            metadata=metadata,
        )
    except Exception as e:
        return {"status": "error", "stage": "log_event", "message": str(e)}

    # Analytics-only click tracking: no routing, no state change.
    if normalized in ANALYTICS_ONLY_EVENTS:
        lead = _fetch_outreach_lead(lead_id)
        if lead:
            current_clicks = int(lead.get("click_count") or 0)
            _update_outreach_lead(
                lead_id,
                {
                    "click_count": current_clicks + 1,
                    "link_clicked": True,
                    "last_updated": _now_iso(),
                },
            )

        return {
            "status": "success",
            "event_type": normalized,
            "route": {"routed_to": "analytics_only", "action": "none"},
            "log": log_result,
        }

    lead = _fetch_outreach_lead(lead_id)

    # If the lead doesn't exist in outreach_leads, keep the event log and exit safely.
    if not lead:
        return {
            "status": "success",
            "event_type": normalized,
            "route": {"routed_to": "none", "action": "lead_not_found"},
            "log": log_result,
        }

    # -----------------------------------------------------------------------
    # SENT
    # -----------------------------------------------------------------------
    if normalized == "sent":
        _update_outreach_lead(
            lead_id,
            {
                "status": "sent",
                "last_email_sent": metadata.get("sent_at") or _now_iso(),
                "last_contacted": metadata.get("sent_at") or _now_iso(),
                "last_updated": _now_iso(),
            },
        )
        return {
            "status": "success",
            "event_type": normalized,
            "route": {"routed_to": "lead_state", "action": "mark_sent"},
            "log": log_result,
        }

    # -----------------------------------------------------------------------
    # OPENED
    # -----------------------------------------------------------------------
    if normalized == "opened":
        current_open_count = int(lead.get("open_count") or 0)

        payload: Dict[str, Any] = {
            "open_count": current_open_count + 1,
            "email_opened": True,
            "last_updated": _now_iso(),
        }

        # Store the first open timestamp once.
        if not lead.get("email_opened_at"):
            payload["email_opened_at"] = _now_naive_iso()

        _update_outreach_lead(lead_id, payload)

        return {
            "status": "success",
            "event_type": normalized,
            "route": {"routed_to": "lead_state", "action": "increment_open_count"},
            "log": log_result,
        }

    # -----------------------------------------------------------------------
    # REPLIED
    # -----------------------------------------------------------------------
    if normalized == "replied":
        current_reply_count = int(lead.get("reply_count") or 0)

        payload = {
            "reply_count": current_reply_count + 1,
            "reply_status": True,
            "replied_at": metadata.get("timestamp") or _now_iso(),
            "last_contacted": metadata.get("timestamp") or _now_iso(),
            "last_updated": _now_iso(),
        }

        _update_outreach_lead(lead_id, payload)

        return {
            "status": "success",
            "event_type": normalized,
            "route": {"routed_to": "lead_state", "action": "increment_reply_count"},
            "log": log_result,
        }

    # -----------------------------------------------------------------------
    # CONVERTED / FAILED / OPT-OUT
    # -----------------------------------------------------------------------
    if normalized in {"converted", "failed", "opt_out", "completed"}:
        payload = {
            "status": normalized,
            "last_updated": _now_iso(),
        }

        if normalized == "converted":
            current_conversion = int(lead.get("conversion_count") or 0)
            payload["conversion_count"] = current_conversion + 1

        _update_outreach_lead(lead_id, payload)

        return {
            "status": "success",
            "event_type": normalized,
            "route": {"routed_to": "lead_state", "action": f"mark_{normalized}"},
            "log": log_result,
        }

    # -----------------------------------------------------------------------
    # Fallback
    # -----------------------------------------------------------------------
    return {
        "status": "success",
        "event_type": normalized,
        "route": {"routed_to": "unhandled", "action": "none"},
        "log": log_result,
    }
