# outreach_engine/core/event_normalizer.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


_EVENT_ALIASES = {
    "open": "opened",
    "email_open": "opened",
    "email_opened": "opened",
    "pixel_open": "opened",
    "click": "clicked",
    "link_click": "clicked",
    "link_clicked": "clicked",
    "email_click": "clicked",
    "reply": "replied",
    "email_reply": "replied",
    "response": "replied",
    "conversion": "converted",
    "convert": "converted",
    "email_sent": "sent",
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_event_type(event_type: Optional[str], source: str = "") -> str:
    raw = (event_type or "").strip().lower()
    if raw in _EVENT_ALIASES:
        return _EVENT_ALIASES[raw]

    src = (source or "").strip().lower()
    if src in {"pixel", "pixel_tracker"}:
        return "opened"
    if src in {"link", "link_tracker", "click", "click_tracker"}:
        return "clicked"
    if src in {"gmail", "gmail_webhook", "reply", "reply_monitor"}:
        return raw or "replied"

    return raw or "unknown"


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _pick(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if payload.get(key) not in (None, ""):
            return payload.get(key)
    return None


def normalize_event(source: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalizes all event sources into:

    {
      "event_type": "",
      "lead_id": "",
      "campaign_id": "",
      "timestamp": "",
      "metadata": {}
    }
    """
    payload = _safe_dict(payload)
    source = (source or "").strip().lower()

    event_type = _normalize_event_type(
        _pick(payload, "event_type", "type", "action"),
        source=source,
    )

    lead_id = _pick(
        payload,
        "lead_id",
        "outreach_lead_id",
        "recipient_id",
        "id",
    )

    campaign_id = _pick(
        payload,
        "campaign_id",
        "campaign",
    )

    timestamp = _pick(
        payload,
        "timestamp",
        "created_at",
        "ts",
        "time",
    ) or _utcnow_iso()

    metadata = dict(payload.get("metadata") or {})

    # Carry source-specific useful fields into metadata
    metadata.setdefault("source", source)
    metadata.setdefault("raw_source", source)
    metadata.setdefault("event_type_raw", _pick(payload, "event_type", "type", "action"))

    for k in ("subject", "body", "snippet", "message", "sender", "from", "email", "url", "ip", "user_agent", "thread_id", "gmail_message_id", "followup_step"):
        if payload.get(k) not in (None, ""):
            metadata.setdefault(k, payload.get(k))

    return {
        "event_type": event_type,
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "timestamp": timestamp,
        "metadata": metadata,
    }


def normalize_gmail_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    return normalize_event("gmail_webhook", payload)


def normalize_pixel_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    return normalize_event("pixel", payload)


def normalize_link_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    return normalize_event("link_tracker", payload)
