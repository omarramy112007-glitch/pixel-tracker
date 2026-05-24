# outreach_engine/core/event_router.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import log_event
from outreach_engine.core.state_machine import (
    apply_transition,
    classify_reply_intent,
    normalize_event_type,
)
from outreach_engine.core.queue import enqueue_followup


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Events that should stop all follow-ups immediately
STOP_EVENTS = {"converted", "failed", "opt_out", "completed"}

# Events that are analytics-only — no routing, no state change
ANALYTICS_ONLY_EVENTS = {"clicked"}

# Terminal / helper mappings
_REPLY_TO_STATE = {
    "interested": "interested",
    "question": "interested",       # any useful reply gets the interested flow
    "not_interested": "completed",
    "unsubscribe": "opt_out",
    "auto_reply": "sent",
    "unknown": "interested",        # default optimistic routing
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _fetch_lead_snapshot(lead_id: Any) -> tuple[str, Optional[Dict[str, Any]]]:
    """
    Returns:
      ("outreach_leads" | "leads" | "", row_or_none)
    """
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return "outreach_leads", res.data[0]
    except Exception:
        pass

    try:
        res = (
            supabase.table("leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return "leads", res.data[0]
    except Exception:
        pass

    return "", None


def _persist_special_state(
    table_name: str,
    lead_id: Any,
    target_state: str,
    current_row: Dict[str, Any],
) -> None:
    """
    We only write extra state changes when the reply intent changes the state
    beyond the default log_event() behavior.
    """
    now = _now_iso()
    target_state = (target_state or "").strip().lower()

    if not table_name:
        return

    if table_name == "outreach_leads":
        payload: Dict[str, Any] = {
            "status": target_state,
            "last_updated": now,
        }

        if target_state == "interested":
            payload["pipeline_stage"] = "Interested"
        elif target_state in {"completed", "opt_out"}:
            payload["pipeline_stage"] = "Closed"

        try:
            supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
        except Exception:
            pass

    elif table_name == "leads":
        payload = {
            "updated_at": now,
        }

        if target_state == "interested":
            payload["pipeline_stage"] = "Interested"
        elif target_state == "completed":
            payload["pipeline_stage"] = "Closed"
        elif target_state == "opt_out":
            payload["pipeline_stage"] = "Closed"

        try:
            supabase.table("leads").update(payload).eq("id", lead_id).execute()
        except Exception:
            pass


def _enqueue_open_followup(lead: Dict[str, Any], campaign_id: int) -> Dict[str, Any]:
    """
    Open-only signal => soft follow-up after a delay.
    """
    lead_id = lead.get("id")
    current_step = int(lead.get("followup_step") or 0)

    # 24h soft follow-up by default
    return enqueue_followup(
        lead_id=lead_id,
        followup_step=current_step + 1,
        delay_hours=24,
        reason="open_signal",
    )


def _enqueue_reply_followup(lead: Dict[str, Any], campaign_id: int, target_state: str) -> Dict[str, Any]:
    """
    Reply that is useful => queue the next message immediately.
    """
    lead_id = lead.get("id")
    current_step = int(lead.get("followup_step") or 0)

    # Immediate queue item; the worker will decide the exact email step from DB state.
    return enqueue_followup(
        lead_id=lead_id,
        followup_step=current_step + 1,
        delay_hours=0,
        reason=f"reply_{target_state}",
    )


def _apply_state_machine(
    lead_snapshot: Dict[str, Any],
    event_type: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Pure in-memory transition for routing decisions.
    """
    lead_copy = dict(lead_snapshot or {})
    _, result = apply_transition(lead_copy, event_type, metadata=metadata)
    return {
        "lead": lead_copy,
        "transition": result,
    }


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
    Single entry point for ALL events in the system.

    Rules:
      - replied   -> reply intent decides interested / completed / opt-out
      - opened    -> soft follow-up only
      - clicked   -> analytics only, no decision layer
      - sent      -> state update only
      - converted -> terminal
      - failed    -> terminal
    """
    if lead_id is None:
        return {"status": "error", "message": "lead_id is required"}
    if campaign_id is None:
        return {"status": "error", "message": "campaign_id is required"}

    normalized = normalize_event_type(event_type)
    metadata = _safe_dict(metadata)

    # 1) Always log the event first
    try:
        log_result = log_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type=normalized,
            metadata=metadata,
        )
    except Exception as e:
        return {"status": "error", "stage": "log_event", "message": str(e)}

    if isinstance(log_result, dict) and log_result.get("status") == "duplicate":
        return {"status": "duplicate", "event_type": normalized}

    # 2) Fetch current lead state
    table_name, lead_row = _fetch_lead_snapshot(lead_id)
    lead_row = lead_row or {}

    # 3) Route by event
    if normalized in ANALYTICS_ONLY_EVENTS:
        return {
            "status": "success",
            "event_type": normalized,
            "route": {"routed_to": "analytics_only", "action": "none"},
            "log": log_result,
        }

    # State machine transition (in-memory)
    state_result = _apply_state_machine(lead_row, normalized, metadata)
    transition = state_result["transition"]
    lead_after = state_result["lead"]

    # Special reply handling: reply intent decides the next state
    if normalized == "replied":
        reply_text = (
            metadata.get("reply_text")
            or metadata.get("body")
            or metadata.get("message")
            or ""
        )
        intent = classify_reply_intent(reply_text, metadata)
        target_state = _REPLY_TO_STATE.get(intent, "interested")

        # Update state machine result to our chosen reply flow
        lead_after["status"] = target_state
        transition.to_state = target_state  # keep the result aligned
        transition.stop_followups = target_state in STOP_EVENTS

        # Persist special state if needed
        _persist_special_state(table_name, lead_id, target_state, lead_row)

        # Queue the next step only for useful replies
        if target_state == "interested":
            enqueue_result = _enqueue_reply_followup(lead_after, campaign_id, target_state)
            return {
                "status": "success",
                "event_type": normalized,
                "intent": intent,
                "state": {
                    "from": transition.from_state,
                    "to": transition.to_state,
                    "changed": transition.changed,
                    "stop_followups": transition.stop_followups,
                },
                "route": {
                    "routed_to": "queue",
                    "action": "enqueue_interested_followup",
                    "result": enqueue_result,
                },
                "log": log_result,
            }

        return {
            "status": "success",
            "event_type": normalized,
            "intent": intent,
            "state": {
                "from": transition.from_state,
                "to": transition.to_state,
                "changed": transition.changed,
                "stop_followups": transition.stop_followups,
            },
            "route": {
                "routed_to": "lead_state",
                "action": f"mark_{target_state}",
            },
            "log": log_result,
        }

    # Opened => soft follow-up only
    if normalized == "opened":
        if transition.to_state in {"opened", "sent"}:
            enqueue_result = _enqueue_open_followup(lead_after, campaign_id)
            return {
                "status": "success",
                "event_type": normalized,
                "state": {
                    "from": transition.from_state,
                    "to": transition.to_state,
                    "changed": transition.changed,
                    "stop_followups": transition.stop_followups,
                },
                "route": {
                    "routed_to": "queue",
                    "action": "enqueue_soft_followup",
                    "result": enqueue_result,
                },
                "log": log_result,
            }

        return {
            "status": "success",
            "event_type": normalized,
            "state": {
                "from": transition.from_state,
                "to": transition.to_state,
                "changed": transition.changed,
                "stop_followups": transition.stop_followups,
            },
            "route": {"routed_to": "lead_state", "action": "no_followup"},
            "log": log_result,
        }

    # Sent / converted / failed => state update only
    if normalized in {"sent", "converted", "failed"}:
        _persist_special_state(table_name, lead_id, transition.to_state, lead_row)
        return {
            "status": "success",
            "event_type": normalized,
            "state": {
                "from": transition.from_state,
                "to": transition.to_state,
                "changed": transition.changed,
                "stop_followups": transition.stop_followups,
            },
            "route": {
                "routed_to": "lead_state",
                "action": f"mark_{transition.to_state}",
            },
            "log": log_result,
        }

    # Default fallback
    return {
        "status": "success",
        "event_type": normalized,
        "state": {
            "from": transition.from_state,
            "to": transition.to_state,
            "changed": transition.changed,
            "stop_followups": transition.stop_followups,
        },
        "route": {"routed_to": "unhandled", "action": "none"},
        "log": log_result,
    }
