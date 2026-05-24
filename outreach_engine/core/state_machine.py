# outreach_engine/core/state_machine.py

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


class LeadState(str, Enum):
    NEW = "new"
    SENT = "sent"
    OPENED = "opened"
    REPLIED = "replied"
    INTERESTED = "interested"
    CONVERTED = "converted"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"
    OPT_OUT = "opt_out"
    COMPLETED = "completed"


class EventType(str, Enum):
    SENT = "sent"
    OPENED = "opened"
    CLICKED = "clicked"
    REPLIED = "replied"
    INTERESTED = "interested"
    CONVERTED = "converted"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"
    OPT_OUT = "opt_out"
    COMPLETED = "completed"


CANONICAL_STATE_ALIASES = {
    "pending": LeadState.NEW.value,
    "new": LeadState.NEW.value,
    "not_contacted": LeadState.NEW.value,
    "not contacted": LeadState.NEW.value,
    "processing": LeadState.SENT.value,
    "sent": LeadState.SENT.value,
    "opened": LeadState.OPENED.value,
    "open": LeadState.OPENED.value,
    "replied": LeadState.REPLIED.value,
    "reply": LeadState.REPLIED.value,
    "interested": LeadState.INTERESTED.value,
    "qualified": LeadState.INTERESTED.value,
    "converted": LeadState.CONVERTED.value,
    "won": LeadState.CONVERTED.value,
    "failed": LeadState.FAILED.value,
    "rate_limited": LeadState.RATE_LIMITED.value,
    "rate limited": LeadState.RATE_LIMITED.value,
    "opt-out": LeadState.OPT_OUT.value,
    "opt_out": LeadState.OPT_OUT.value,
    "unsubscribed": LeadState.OPT_OUT.value,
    "completed": LeadState.COMPLETED.value,
}


EVENT_ALIASES = {
    "open": EventType.OPENED.value,
    "email_open": EventType.OPENED.value,
    "email_opened": EventType.OPENED.value,
    "click": EventType.CLICKED.value,
    "link_click": EventType.CLICKED.value,
    "reply": EventType.REPLIED.value,
    "response": EventType.REPLIED.value,
    "interested_reply": EventType.INTERESTED.value,
    "conversion": EventType.CONVERTED.value,
    "convert": EventType.CONVERTED.value,
    "email_sent": EventType.SENT.value,
    "bounce": EventType.FAILED.value,
    "bounced": EventType.FAILED.value,
    "unsubscribe": EventType.OPT_OUT.value,
    "unsubscribed": EventType.OPT_OUT.value,
}


# "from_state" -> allowed next states after a given event
TRANSITIONS: Dict[str, Dict[str, str]] = {
    LeadState.NEW.value: {
        EventType.SENT.value: LeadState.SENT.value,
        EventType.FAILED.value: LeadState.FAILED.value,
        EventType.OPT_OUT.value: LeadState.OPT_OUT.value,
    },
    LeadState.SENT.value: {
        EventType.OPENED.value: LeadState.OPENED.value,
        EventType.REPLIED.value: LeadState.REPLIED.value,
        EventType.INTERESTED.value: LeadState.INTERESTED.value,
        EventType.FAILED.value: LeadState.FAILED.value,
        EventType.RATE_LIMITED.value: LeadState.RATE_LIMITED.value,
        EventType.OPT_OUT.value: LeadState.OPT_OUT.value,
    },
    LeadState.OPENED.value: {
        EventType.REPLIED.value: LeadState.REPLIED.value,
        EventType.INTERESTED.value: LeadState.INTERESTED.value,
        EventType.SENT.value: LeadState.SENT.value,  # follow-up sent after open
        EventType.FAILED.value: LeadState.FAILED.value,
        EventType.RATE_LIMITED.value: LeadState.RATE_LIMITED.value,
        EventType.OPT_OUT.value: LeadState.OPT_OUT.value,
    },
    LeadState.REPLIED.value: {
        EventType.INTERESTED.value: LeadState.INTERESTED.value,
        EventType.CONVERTED.value: LeadState.CONVERTED.value,
        EventType.COMPLETED.value: LeadState.COMPLETED.value,
        EventType.OPT_OUT.value: LeadState.OPT_OUT.value,
    },
    LeadState.INTERESTED.value: {
        EventType.CONVERTED.value: LeadState.CONVERTED.value,
        EventType.COMPLETED.value: LeadState.COMPLETED.value,
        EventType.FAILED.value: LeadState.FAILED.value,
        EventType.OPT_OUT.value: LeadState.OPT_OUT.value,
    },
    LeadState.RATE_LIMITED.value: {
        EventType.SENT.value: LeadState.SENT.value,
        EventType.OPENED.value: LeadState.OPENED.value,
        EventType.REPLIED.value: LeadState.REPLIED.value,
        EventType.OPT_OUT.value: LeadState.OPT_OUT.value,
        EventType.FAILED.value: LeadState.FAILED.value,
    },
    LeadState.FAILED.value: {
        EventType.SENT.value: LeadState.SENT.value,
        EventType.OPT_OUT.value: LeadState.OPT_OUT.value,
    },
    LeadState.CONVERTED.value: {},
    LeadState.OPT_OUT.value: {},
    LeadState.COMPLETED.value: {},
}


STOP_FOLLOWUP_STATES = {
    LeadState.REPLIED.value,
    LeadState.INTERESTED.value,
    LeadState.CONVERTED.value,
    LeadState.OPT_OUT.value,
    LeadState.FAILED.value,
    LeadState.COMPLETED.value,
}


@dataclass
class TransitionResult:
    lead_id: Any
    from_state: str
    event_type: str
    to_state: str
    changed: bool
    stop_followups: bool
    metadata: Dict[str, Any]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_state(value: Any) -> str:
    text = str(value or "").strip().lower()
    return CANONICAL_STATE_ALIASES.get(text, text or LeadState.NEW.value)


def normalize_event_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    return EVENT_ALIASES.get(text, text)


def can_transition(from_state: Any, event_type: Any) -> bool:
    state = normalize_state(from_state)
    event = normalize_event_type(event_type)

    if state in TRANSITIONS and event in TRANSITIONS[state]:
        return True

    # Allow idempotent events when the state already matches the event meaning.
    if state == normalize_state(event):
        return True

    return False


def next_state(from_state: Any, event_type: Any) -> str:
    state = normalize_state(from_state)
    event = normalize_event_type(event_type)

    if state in TRANSITIONS and event in TRANSITIONS[state]:
        return TRANSITIONS[state][event]

    # Idempotent / self-transition behavior
    if state == normalize_state(event):
        return state

    # Special safety fallback:
    # reply-related signals should always win over weaker states
    if event == EventType.REPLIED.value:
        return LeadState.REPLIED.value
    if event == EventType.INTERESTED.value:
        return LeadState.INTERESTED.value
    if event == EventType.CONVERTED.value:
        return LeadState.CONVERTED.value
    if event == EventType.OPT_OUT.value:
        return LeadState.OPT_OUT.value
    if event == EventType.FAILED.value:
        return LeadState.FAILED.value
    if event == EventType.RATE_LIMITED.value:
        return LeadState.RATE_LIMITED.value
    if event == EventType.OPENED.value:
        return LeadState.OPENED.value
    if event == EventType.SENT.value:
        return LeadState.SENT.value

    return state


def should_stop_followups(state: Any) -> bool:
    return normalize_state(state) in STOP_FOLLOWUP_STATES


def apply_transition(
    lead: Dict[str, Any],
    event_type: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> TransitionResult:
    """
    Pure state transition function.
    Takes a lead dict and returns the updated transition result.
    Does not write to DB by itself.
    """
    metadata = dict(metadata or {})
    from_state = normalize_state(
        lead.get("status")
        or lead.get("outreach_status")
        or lead.get("pipeline_stage")
        or LeadState.NEW.value
    )
    event = normalize_event_type(event_type)
    to_state = next_state(from_state, event)
    changed = to_state != from_state

    updated_metadata = {
        **metadata,
        "state_transitioned_at": _utc_now_iso(),
        "from_state": from_state,
        "event_type": event,
        "to_state": to_state,
    }

    lead["status"] = to_state
    lead["outreach_status"] = to_state
    lead["pipeline_stage"] = to_state.capitalize()
    lead["metadata"] = {**(lead.get("metadata") or {}), **updated_metadata}

    return TransitionResult(
        lead_id=lead.get("id"),
        from_state=from_state,
        event_type=event,
        to_state=to_state,
        changed=changed,
        stop_followups=should_stop_followups(to_state),
        metadata=updated_metadata,
    )


def is_terminal_state(state: Any) -> bool:
    return normalize_state(state) in {
        LeadState.CONVERTED.value,
        LeadState.OPT_OUT.value,
        LeadState.COMPLETED.value,
        LeadState.FAILED.value,
    }


def is_reply_state(state: Any) -> bool:
    return normalize_state(state) in {
        LeadState.REPLIED.value,
        LeadState.INTERESTED.value,
        LeadState.CONVERTED.value,
        LeadState.COMPLETED.value,
    }


def classify_reply_intent(reply_text: Optional[str], metadata: Optional[Dict[str, Any]] = None) -> str:
    """
    Lightweight deterministic reply classifier.
    Returns: interested / question / not_interested / unsubscribe / auto_reply / unknown
    """
    text = (reply_text or "").strip().lower()
    metadata = metadata or {}

    if metadata.get("auto_reply") is True:
        return "auto_reply"

    unsubscribe_markers = [
        "unsubscribe",
        "remove me",
        "stop emailing",
        "do not contact",
        "no longer interested",
        "not interested",
    ]
    if any(marker in text for marker in unsubscribe_markers):
        return "unsubscribe"

    interested_markers = [
        "sounds good",
        "interested",
        "let's talk",
        "let's chat",
        "send me",
        "tell me more",
        "breakdown",
        "proposal",
    ]
    if any(marker in text for marker in interested_markers):
        return "interested"

    question_markers = ["?", "how does it work", "what is", "can you"]
    if any(marker in text for marker in question_markers):
        return "question"

    not_interested_markers = [
        "not interested",
        "not now",
        "no thanks",
        "already have",
        "we're good",
        "not a fit",
    ]
    if any(marker in text for marker in not_interested_markers):
        return "not_interested"

    return "unknown"


def reply_intent_to_state(intent: str) -> str:
    intent = (intent or "").strip().lower()
    if intent == "interested":
        return LeadState.INTERESTED.value
    if intent == "question":
        return LeadState.REPLIED.value
    if intent == "not_interested":
        return LeadState.COMPLETED.value
    if intent == "unsubscribe":
        return LeadState.OPT_OUT.value
    if intent == "auto_reply":
        return LeadState.SENT.value
    return LeadState.REPLIED.value


def route_event_state(
    lead: Dict[str, Any],
    event_type: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], TransitionResult]:
    """
    Convenience wrapper:
    - applies transition
    - mutates lead dict
    - returns updated lead + result
    """
    metadata = dict(metadata or {})
    result = apply_transition(lead, event_type, metadata=metadata)
    lead["updated_at"] = _utc_now_iso()
    return lead, result


def should_trigger_followup(from_state: Any, to_state: Any, event_type: Any) -> bool:
    """
    Rules:
    - opened -> can trigger soft follow-up
    - sent -> can trigger wait-state only
    - replied/interested/converted/opt-out -> never trigger automated follow-ups
    """
    from_state_n = normalize_state(from_state)
    to_state_n = normalize_state(to_state)
    event_n = normalize_event_type(event_type)

    if to_state_n in STOP_FOLLOWUP_STATES:
        return False

    if event_n == EventType.OPENED.value and from_state_n in {
        LeadState.SENT.value,
        LeadState.NEW.value,
        LeadState.RATE_LIMITED.value,
    }:
        return True

    if event_n == EventType.SENT.value and from_state_n in {LeadState.NEW.value, LeadState.FAILED.value}:
        return False

    return False


def transition_allowed(from_state: Any, event_type: Any) -> bool:
    return can_transition(from_state, event_type)


def get_next_action(from_state: Any, event_type: Any) -> str:
    """
    Returns a simple action hint for the scheduler:
    - send_followup
    - stop
    - wait
    """
    state = normalize_state(from_state)
    event = normalize_event_type(event_type)

    if state in STOP_FOLLOWUP_STATES:
        return "stop"

    if event == EventType.OPENED.value:
        return "send_followup"

    if event == EventType.SENT.value:
        return "wait"

    if event == EventType.REPLIED.value:
        return "stop"

    return "wait"
