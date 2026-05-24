# outreach_engine/core/event_router.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from outreach_engine.tracking.event_repository import log_event


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Events that should stop all follow-ups immediately
REPLY_EVENTS = {"replied"}

# Events that are analytics-only — no routing, no state change
ANALYTICS_ONLY_EVENTS = {"clicked"}

# Canonical event type map
_EVENT_TYPE_MAP: Dict[str, str] = {
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

_EVENT_TYPE_SUFFIXES: Dict[str, str] = {
    "_sent": "sent",
    "_opened": "opened",
    "_clicked": "clicked",
    "_replied": "replied",
    "_converted": "converted",
    "_failed": "failed",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_event_type(event_type: str) -> str:
    """Canonicalize any event_type string to one of the known types."""
    et = (event_type or "").lower().strip()

    if et in _EVENT_TYPE_MAP:
        return _EVENT_TYPE_MAP[et]

    for suffix, canonical in _EVENT_TYPE_SUFFIXES.items():
        if et.endswith(suffix):
            return canonical

    return et


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def handle_event(
    event_type: str,
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Single entry point for ALL events in the system.

    Steps:
      1. Validate required fields
      2. Normalize event type
      3. Log event (always)
      4. Route to the correct handler based on event type
      5. Return routing result

    Rules:
      - replied  → route to reply_monitor (stops all follow-ups)
      - opened   → route to follow_up_manager as a soft signal
      - sent     → update lead state only
      - clicked  → analytics only, NO routing
      - converted/failed → update lead state only
    """
    if lead_id is None:
        return {"status": "error", "message": "lead_id is required"}
    if campaign_id is None:
        return {"status": "error", "message": "campaign_id is required"}

    normalized = normalize_event_type(event_type)
    metadata = metadata or {}

    # --- Step 1: Always log the event ---
    try:
        log_result = log_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type=normalized,
            metadata=metadata,
        )
    except Exception as e:
        return {"status": "error", "stage": "log_event", "message": str(e)}

    # Duplicate — stop here, no further routing
    if isinstance(log_result, dict) and log_result.get("status") == "duplicate":
        return {"status": "duplicate", "event_type": normalized}

    # --- Step 2: Route ---
    route_result = _route(normalized, lead_id, campaign_id, metadata)

    return {
        "status": "success",
        "event_type": normalized,
        "log": log_result,
        "route": route_result,
    }


def _route(
    event_type: str,
    lead_id: int,
    campaign_id: int,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Pure routing switch. Calls the right handler per event type.
    No business logic lives here — just dispatch.
    """

    # --- Reply: highest priority — stop everything ---
    if event_type in REPLY_EVENTS:
        return _route_to_reply_monitor(lead_id, campaign_id, metadata)

    # --- Clicked: analytics only, no state change ---
    if event_type in ANALYTICS_ONLY_EVENTS:
        return {"routed_to": "analytics_only", "action": "none"}

    # --- Opened: soft signal, may schedule soft follow-up ---
    if event_type == "opened":
        return _route_open_signal(lead_id, campaign_id, metadata)

    # --- Sent: mark state ---
    if event_type == "sent":
        return _route_sent(lead_id, campaign_id, metadata)

    # --- Converted / Failed: terminal state updates ---
    if event_type in {"converted", "failed"}:
        return _route_terminal(event_type, lead_id, campaign_id, metadata)

    return {"routed_to": "unhandled", "event_type": event_type}


# ---------------------------------------------------------------------------
# Individual route handlers
# ---------------------------------------------------------------------------

def _route_to_reply_monitor(
    lead_id: int,
    campaign_id: int,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    A reply was detected → hand off to reply_monitor.
    reply_monitor is responsible for:
      - detecting reply intent
      - updating lead state to 'replied'
      - cancelling all pending follow-ups
    """
    try:
        # Import here to avoid circular imports
        from outreach_engine.core.reply_monitor import process_reply_event

        result = process_reply_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            metadata=metadata,
        )
        return {"routed_to": "reply_monitor", "result": result}
    except ImportError:
        # reply_monitor may not expose process_reply_event yet — fall back to
        # direct lead state update so replies are never silently dropped
        from outreach_engine.core.lead_manager import mark_replied_by_id
        mark_replied_by_id(lead_id, campaign_id)
        return {"routed_to": "reply_monitor_fallback", "action": "mark_replied"}
    except Exception as e:
        return {"routed_to": "reply_monitor", "error": str(e)}


def _route_open_signal(
    lead_id: int,
    campaign_id: int,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    An open was detected.
    Rule: open = signal only.
    follow_up_manager decides whether to schedule a soft follow-up based
    on lead state — the router does NOT make that decision.
    """
    try:
        from outreach_engine.processors.follow_up_manager import on_open_signal

        result = on_open_signal(lead_id=lead_id, campaign_id=campaign_id)
        return {"routed_to": "follow_up_manager", "signal": "open", "result": result}
    except ImportError:
        return {"routed_to": "follow_up_manager", "signal": "open", "action": "skipped_no_handler"}
    except Exception as e:
        return {"routed_to": "follow_up_manager", "signal": "open", "error": str(e)}


def _route_sent(
    lead_id: int,
    campaign_id: int,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Email was sent — update lead state and schedule the next follow-up window.
    """
    try:
        from outreach_engine.core.lead_manager import mark_sent_by_id

        mark_sent_by_id(lead_id, campaign_id)
        return {"routed_to": "lead_manager", "action": "mark_sent"}
    except ImportError:
        return {"routed_to": "lead_manager", "action": "skipped_no_handler"}
    except Exception as e:
        return {"routed_to": "lead_manager", "action": "mark_sent", "error": str(e)}


def _route_terminal(
    event_type: str,
    lead_id: int,
    campaign_id: int,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Terminal events (converted, failed) — update lead state only.
    No follow-ups are ever sent after these.
    """
    try:
        from outreach_engine.core.lead_manager import mark_converted_by_id, mark_failed_by_id

        if event_type == "converted":
            mark_converted_by_id(lead_id, campaign_id)
            return {"routed_to": "lead_manager", "action": "mark_converted"}

        if event_type == "failed":
            mark_failed_by_id(lead_id, campaign_id)
            return {"routed_to": "lead_manager", "action": "mark_failed"}

    except ImportError:
        return {"routed_to": "lead_manager", "action": "skipped_no_handler"}
    except Exception as e:
        return {"routed_to": "lead_manager", "action": event_type, "error": str(e)}

    return {"routed_to": "unhandled"}
