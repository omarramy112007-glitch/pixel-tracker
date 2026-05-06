# outreach_engine/core/lead_manager.py

from __future__ import annotations

from typing import Optional, Dict, List, Any
from datetime import datetime

from outreach_engine.database.supabase_client import supabase

TABLE_NAME = "outreach_leads"

TERMINAL_STATUSES = {"replied", "converted", "opt-out", "failed"}
ACTIVE_STATUSES = {"new", "pending", "sent", "processing"}

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
}


def _strip_or_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    return value


def _normalize_status(status: Any) -> str:
    return (status or "").strip().lower() if isinstance(status, str) else str(status or "").strip().lower()


def _normalize_update_data(data: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        payload[key] = value

    if "last_email_sent_at" in payload and "last_email_sent" not in payload:
        payload["last_email_sent"] = payload.pop("last_email_sent_at")

    return payload


def is_terminal_status(status: Any) -> bool:
    return _normalize_status(status) in TERMINAL_STATUSES


def can_send_initial(lead: Dict[str, Any]) -> bool:
    status = _normalize_status(lead.get("status"))
    return status in {"new", "pending"}


def can_send_followup(lead: Dict[str, Any]) -> bool:
    status = _normalize_status(lead.get("status"))
    return status == "sent"


def _maybe_reset_tracking_fields(
    payload: Dict[str, Any],
    reset_tracking: bool = False,
) -> Dict[str, Any]:
    if not reset_tracking:
        return payload

    merged = dict(payload)
    for key, value in RESET_TRACKING_FIELDS.items():
        merged[key] = value
    return merged


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
    metadata = metadata or {}

    email = _strip_or_none(email)
    first_name = _strip_or_none(first_name)
    last_name = _strip_or_none(last_name)
    company = _strip_or_none(company)
    industry = _strip_or_none(industry)
    lead_source = _strip_or_none(lead_source)
    status = _strip_or_none(status) or "pending"

    if not email:
        raise ValueError("email is required")
    if campaign_id is None:
        raise ValueError("campaign_id is required")

    now = datetime.utcnow().isoformat()

    existing = (
        supabase.table(TABLE_NAME)
        .select("*")
        .eq("email", email)
        .eq("campaign_id", campaign_id)
        .limit(1)
        .execute()
    )

    update_data = _normalize_update_data({
        "first_name": first_name,
        "last_name": last_name,
        "company": company,
        "industry": industry,
        "lead_source": lead_source,
        "followup_step": followup_step,
        "status": status,
        "metadata": metadata,
        "last_updated": now,
    })

    if reset_tracking:
        update_data = _maybe_reset_tracking_fields(update_data, reset_tracking=True)

    if existing.data and len(existing.data) > 0:
        supabase.table(TABLE_NAME) \
            .update(update_data) \
            .eq("email", email) \
            .eq("campaign_id", campaign_id) \
            .execute()
        return "updated"

    insert_data = update_data.copy()
    insert_data["email"] = email
    insert_data["campaign_id"] = campaign_id
    insert_data["created_at"] = now

    if reset_tracking:
        insert_data = _maybe_reset_tracking_fields(insert_data, reset_tracking=True)

    supabase.table(TABLE_NAME).insert(insert_data).execute()
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


def get_lead(email: str, campaign_id: int) -> Optional[Dict[str, Any]]:
    email = _strip_or_none(email)
    if not email or campaign_id is None:
        return None

    result = (
        supabase.table(TABLE_NAME)
        .select("*")
        .eq("email", email)
        .eq("campaign_id", campaign_id)
        .limit(1)
        .execute()
    )

    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


def update_lead_status(
    email: str,
    campaign_id: int,
    followup_step: Optional[int] = None,
    status: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    reset_tracking: bool = False,
) -> None:
    email = _strip_or_none(email)
    if not email or campaign_id is None:
        return

    now = datetime.utcnow().isoformat()
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
        update_data = _maybe_reset_tracking_fields(update_data, reset_tracking=True)

    if not update_data:
        return

    if _normalize_status(update_data.get("status")) == "sent" and "last_email_sent" not in update_data:
        update_data["last_email_sent"] = now

    update_data["last_updated"] = now

    supabase.table(TABLE_NAME) \
        .update(update_data) \
        .eq("email", email) \
        .eq("campaign_id", campaign_id) \
        .execute()


def get_campaign_leads(campaign_id: int) -> List[Dict[str, Any]]:
    result = (
        supabase.table(TABLE_NAME)
        .select("*")
        .eq("campaign_id", campaign_id)
        .execute()
    )

    return result.data if result.data else []


def mark_replied(email: str, campaign_id: int) -> None:
    update_lead_status(email, campaign_id, status="replied")


def mark_failed(email: str, campaign_id: int, reason: str = "") -> None:
    update_lead_status(
        email,
        campaign_id,
        status="failed",
        metadata={"reason": reason},
    )