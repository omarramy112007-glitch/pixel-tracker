# outreach_engine/core/lead_manager.py

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from outreach_engine.database.supabase_client import supabase

TABLE_NAME = "outreach_leads"

# ---------------------------------------------------------------------------
# State definitions
# ---------------------------------------------------------------------------

_STATE_PRIORITY = [
    "new",
    "pending",
    "not_contacted",
    "processing",
    "rate_limited",
    "contacted",
    "sent",
    "followup_no_open",
    "followup_soft_open",
    "interested_followup",
    "opened",       # legacy/compat only
    "replied",
    "interested",
    "completed",
    "converted",
    "failed",
    "opt-out",
    "unsubscribed",
    "cancelled",
]

TERMINAL_STATUSES = {
    "replied",
    "converted",
    "opt-out",
    "unsubscribed",
    "failed",
    "cancelled",
    "completed",
}

ACTIVE_FOLLOWUP_STATUSES = {
    "sent",
    "followup_no_open",
    "followup_soft_open",
    "interested_followup",
}

ACTIVE_STATUSES = {
    "new",
    "pending",
    "not_contacted",
    "processing",
    "rate_limited",
    "contacted",
    "sent",
    "followup_no_open",
    "followup_soft_open",
    "interested_followup",
}

RESET_TRACKING_FIELDS = {
    "thread_id": None,
    "gmail_message_id": None,
    "replied_at": None,
    "reply_status": None,
    "email_opened": False,
    "email_opened_at": None,
    "link_clicked": False,
    "link_clicked_at": None,
    "last_contacted": None,
    "last_email_sent": None,
    "next_followup": None,
}

FOLLOWUP_DELAY_HOURS = {
    "followup_no_open": 48,
    "followup_soft_open": 24,
    "interested_followup": 12,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_or_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    return value


def _normalize_status(status: Any) -> str:
    return (str(status or "")).strip().lower().replace("_", "-")


def _is_terminal(status: Any) -> bool:
    return _normalize_status(status) in TERMINAL_STATUSES


def _state_rank(status: str) -> int:
    s = _normalize_status(status)
    try:
        return _STATE_PRIORITY.index(s)
    except ValueError:
        return -1


def _can_overwrite(current: str, new: str) -> bool:
    """
    Allow overwriting only with same-or-higher priority states.
    """
    current_rank = _state_rank(current)
    new_rank = _state_rank(new)
    return new_rank >= current_rank


def _normalize_update_data(data: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        payload[key] = value

    if "last_email_sent_at" in payload and "last_email_sent" not in payload:
        payload["last_email_sent"] = payload.pop("last_email_sent_at")

    return payload


def _merge_metadata(existing: Any, extra: Dict[str, Any]) -> Dict[str, Any]:
    base = existing if isinstance(existing, dict) else {}
    merged = dict(base)
    merged.update(extra)
    return merged


def _followup_delay_for(status: str, delay_hours: Optional[int] = None) -> int:
    if delay_hours is not None:
        return max(0, int(delay_hours))
    return FOLLOWUP_DELAY_HOURS.get(_normalize_status(status), 24)


# ---------------------------------------------------------------------------
# Low-level DB operations
# ---------------------------------------------------------------------------

def _fetch_by_email(email: str, campaign_id: int) -> Optional[Dict[str, Any]]:
    try:
        res = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("email", email)
            .eq("campaign_id", campaign_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"⚠️ _fetch_by_email failed for {email}: {e}")
        return None


def _fetch_by_id(lead_id: int, campaign_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    try:
        query = supabase.table(TABLE_NAME).select("*").eq("id", lead_id)
        if campaign_id is not None:
            query = query.eq("campaign_id", campaign_id)
        res = query.limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"⚠️ _fetch_by_id failed for id={lead_id}: {e}")
        return None


def _update_by_email(email: str, campaign_id: int, payload: Dict[str, Any]) -> None:
    try:
        supabase.table(TABLE_NAME) \
            .update(payload) \
            .eq("email", email) \
            .eq("campaign_id", campaign_id) \
            .execute()
    except Exception as e:
        print(f"⚠️ _update_by_email failed for {email}: {e}")


def _update_by_id(lead_id: int, payload: Dict[str, Any]) -> None:
    try:
        supabase.table(TABLE_NAME) \
            .update(payload) \
            .eq("id", lead_id) \
            .execute()
    except Exception as e:
        print(f"⚠️ _update_by_id failed for id={lead_id}: {e}")


# ---------------------------------------------------------------------------
# Named state transition methods
# ---------------------------------------------------------------------------

def mark_sent_by_id(lead_id: int, campaign_id: int) -> None:
    """
    Transition: any eligible state → sent
    """
    row = _fetch_by_id(lead_id, campaign_id)
    if not row:
        return

    current = _normalize_status(row.get("status"))
    if _is_terminal(current):
        return

    now = _now_iso()
    payload = {
        "status": "sent",
        "last_email_sent": now,
        "last_contacted": now,
        "last_updated": now,
    }
    _update_by_id(lead_id, payload)


def mark_followup_variant_by_id(
    lead_id: int,
    campaign_id: int,
    variant: str,
    delay_hours: Optional[int] = None,
) -> None:
    """
    Transition: sent / followup_* → followup variant label

    This keeps the lead eligible for later automation while still recording
    which follow-up type was last used.
    """
    variant = _normalize_status(variant)
    if variant not in ACTIVE_FOLLOWUP_STATUSES:
        raise ValueError(f"Invalid follow-up variant: {variant}")

    row = _fetch_by_id(lead_id, campaign_id)
    if not row:
        return

    current = _normalize_status(row.get("status"))
    if _is_terminal(current):
        return

    now = datetime.now(timezone.utc)
    next_followup_at = now + timedelta(hours=_followup_delay_for(variant, delay_hours))

    metadata = _merge_metadata(row.get("metadata"), {
        "last_followup_variant": variant,
        "last_followup_at": now.isoformat(),
    })

    current_step = int(row.get("followup_step") or 0)

    _update_by_id(lead_id, {
        "status": variant,
        "followup_step": current_step + 1,
        "last_email_sent": now.isoformat(),
        "last_contacted": now.isoformat(),
        "next_followup": next_followup_at.isoformat(),
        "metadata": metadata,
        "last_updated": now.isoformat(),
    })


def mark_replied_by_id(lead_id: int, campaign_id: int) -> None:
    """
    Transition: active → replied.
    Clears next_followup to stop automated scheduling.
    """
    row = _fetch_by_id(lead_id, campaign_id)
    if not row:
        return

    current = _normalize_status(row.get("status"))
    if current in {"converted", "opt-out", "unsubscribed"}:
        return

    now = _now_iso()
    reply_count = int(row.get("reply_count") or 0)

    _update_by_id(lead_id, {
        "status": "replied",
        "reply_count": reply_count + 1,
        "reply_status": True,
        "replied_at": now,
        "next_followup": None,
        "last_updated": now,
    })


def mark_interested_by_id(lead_id: int, campaign_id: int) -> None:
    """
    Transition: replied → interested
    """
    now = _now_iso()
    _update_by_id(lead_id, {
        "status": "interested",
        "next_followup": None,
        "last_updated": now,
    })


def mark_converted_by_id(lead_id: int, campaign_id: int) -> None:
    """
    Transition: any → converted (terminal)
    """
    now = _now_iso()
    _update_by_id(lead_id, {
        "status": "converted",
        "next_followup": None,
        "last_updated": now,
    })


def mark_failed_by_id(lead_id: int, campaign_id: int, reason: str = "") -> None:
    """
    Transition: any → failed (terminal)
    """
    now = _now_iso()
    payload: Dict[str, Any] = {
        "status": "failed",
        "next_followup": None,
        "last_updated": now,
    }
    if reason:
        payload["metadata"] = {"failure_reason": reason}
    _update_by_id(lead_id, payload)


def mark_opt_out_by_id(lead_id: int, campaign_id: int) -> None:
    """
    Transition: any → opt-out (terminal)
    """
    now = _now_iso()
    _update_by_id(lead_id, {
        "status": "opt-out",
        "next_followup": None,
        "last_updated": now,
    })


# ---------------------------------------------------------------------------
# Email-based wrappers
# ---------------------------------------------------------------------------

def mark_sent(email: str, campaign_id: int) -> None:
    row = _fetch_by_email(email, campaign_id)
    if row:
        mark_sent_by_id(row["id"], campaign_id)


def mark_replied(email: str, campaign_id: int) -> None:
    row = _fetch_by_email(email, campaign_id)
    if row:
        mark_replied_by_id(row["id"], campaign_id)


def mark_interested(email: str, campaign_id: int) -> None:
    row = _fetch_by_email(email, campaign_id)
    if row:
        mark_interested_by_id(row["id"], campaign_id)


def mark_converted(email: str, campaign_id: int) -> None:
    row = _fetch_by_email(email, campaign_id)
    if row:
        mark_converted_by_id(row["id"], campaign_id)


def mark_failed(email: str, campaign_id: int, reason: str = "") -> None:
    row = _fetch_by_email(email, campaign_id)
    if row:
        mark_failed_by_id(row["id"], campaign_id, reason)


def mark_followup_variant(email: str, campaign_id: int, variant: str, delay_hours: Optional[int] = None) -> None:
    row = _fetch_by_email(email, campaign_id)
    if row:
        mark_followup_variant_by_id(row["id"], campaign_id, variant, delay_hours=delay_hours)


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def get_lead(email: str, campaign_id: int) -> Optional[Dict[str, Any]]:
    email = _strip_or_none(email)
    if not email or campaign_id is None:
        return None
    return _fetch_by_email(email, campaign_id)


def get_campaign_leads(campaign_id: int) -> List[Dict[str, Any]]:
    try:
        res = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
        )
        return res.data if res.data else []
    except Exception as e:
        print(f"⚠️ get_campaign_leads failed: {e}")
        return []


def can_send_initial(lead: Dict[str, Any]) -> bool:
    return _normalize_status(lead.get("status")) in {"new", "pending", "not_contacted"}


def can_send_followup(lead: Dict[str, Any]) -> bool:
    return _normalize_status(lead.get("status")) in ACTIVE_FOLLOWUP_STATUSES


# ---------------------------------------------------------------------------
# Add / Update operations
# ---------------------------------------------------------------------------

def add_or_update_lead(
    email: str,
    campaign_id: int,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    company: Optional[str] = None,
    industry: Optional[str] = None,
    lead_source: Optional[str] = None,
    followup_step: int = 0,
    status: str = "pending",
    metadata: Optional[Dict[str, Any]] = None,
    reset_tracking: bool = False,
) -> str:
    """
    Insert or update a lead. Returns 'inserted' or 'updated'.
    """
    email = _strip_or_none(email)
    if not email:
        raise ValueError("email is required")
    if campaign_id is None:
        raise ValueError("campaign_id is required")

    now = _now_iso()
    metadata = metadata or {}
    status = _strip_or_none(status) or "pending"

    update_data = _normalize_update_data({
        "first_name": _strip_or_none(first_name),
        "last_name": _strip_or_none(last_name),
        "company": _strip_or_none(company),
        "industry": _strip_or_none(industry),
        "lead_source": _strip_or_none(lead_source),
        "followup_step": followup_step,
        "status": status,
        "metadata": metadata,
        "last_updated": now,
    })

    if reset_tracking:
        update_data.update(RESET_TRACKING_FIELDS)

    existing = _fetch_by_email(email, campaign_id)

    if existing:
        _update_by_email(email, campaign_id, update_data)
        return "updated"

    insert_data = {**update_data, "email": email, "campaign_id": campaign_id, "created_at": now}
    try:
        supabase.table(TABLE_NAME).insert(insert_data).execute()
    except Exception as e:
        print(f"⚠️ add_or_update_lead insert failed: {e}")
    return "inserted"


def bulk_add_or_update(leads: List[Dict[str, Any]], campaign_id: int) -> None:
    for lead in leads:
        if not lead.get("email"):
            continue
        add_or_update_lead(
            email=lead["email"],
            campaign_id=campaign_id,
            first_name=lead.get("first_name"),
            last_name=lead.get("last_name"),
            company=lead.get("company"),
            industry=lead.get("industry"),
            lead_source=lead.get("lead_source"),
            followup_step=lead.get("followup_step", 0),
            status=lead.get("status", "pending"),
            metadata=lead.get("metadata", {}),
            reset_tracking=bool(lead.get("reset_tracking", False)),
        )


def update_lead_status(
    email: str,
    campaign_id: int,
    followup_step: Optional[int] = None,
    status: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    reset_tracking: bool = False,
) -> None:
    """
    Generic status update — prefer named methods above when possible.
    """
    email = _strip_or_none(email)
    if not email or campaign_id is None:
        return

    now = _now_iso()
    update_data: Dict[str, Any] = {}

    if data and isinstance(data, dict):
        update_data.update(data)
    if followup_step is not None:
        update_data["followup_step"] = followup_step
    if status is not None:
        update_data["status"] = status
    if metadata is not None:
        update_data["metadata"] = metadata

    update_data = _normalize_update_data(update_data)

    if reset_tracking:
        update_data.update(RESET_TRACKING_FIELDS)

    if not update_data:
        return

    if _normalize_status(update_data.get("status")) == "sent" and "last_email_sent" not in update_data:
        update_data["last_email_sent"] = now

    update_data["last_updated"] = now
    _update_by_email(email, campaign_id, update_data)
