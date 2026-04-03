# outreach_engine/core/lead_manager.py

from typing import Optional, Dict, List, Any
from datetime import datetime
from outreach_engine.database.supabase_client import supabase

TABLE_NAME = "outreach_leads"


def _strip_or_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    return value


def _normalize_update_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Keep payloads aligned with the outreach_leads schema.
    """
    payload = {}
    for key, value in data.items():
        if value is None:
            continue
        payload[key] = value

    if "last_email_sent_at" in payload and "last_email_sent" not in payload:
        payload["last_email_sent"] = payload.pop("last_email_sent_at")

    return payload


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
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    """
    Insert a new lead or update an existing one (deduplication by email + campaign).
    Returns: 'inserted' or 'updated'
    """
    if metadata is None:
        metadata = {}

    email = _strip_or_none(email)
    first_name = _strip_or_none(first_name)
    last_name = _strip_or_none(last_name)
    company = _strip_or_none(company)
    industry = _strip_or_none(industry)
    lead_source = _strip_or_none(lead_source)

    now = datetime.utcnow().isoformat()

    existing = (
        supabase.table(TABLE_NAME)
        .select("*")
        .eq("email", email)
        .eq("campaign_id", campaign_id)
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

    supabase.table(TABLE_NAME).insert(insert_data).execute()
    return "inserted"


def bulk_add_or_update(leads: List[Dict[str, Any]], campaign_id: int) -> None:
    """
    Insert or update multiple leads safely.
    Each lead dict must contain at least: email
    Optional: first_name, last_name, company, industry, lead_source,
              followup_step, status, metadata
    """
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
        )


def get_lead(email: str, campaign_id: int) -> Optional[Dict[str, Any]]:
    result = (
        supabase.table(TABLE_NAME)
        .select("*")
        .eq("email", email)
        .eq("campaign_id", campaign_id)
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
) -> None:
    """
    Update a lead's follow-up step, status, metadata, and any extra fields.

    Supports both:
      - update_lead_status(..., followup_step=1, status="sent", metadata={...})
      - update_lead_status(..., data={...})  # backward compatibility
    """
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

    if not update_data:
        return

    if update_data.get("status") == "sent" and "last_email_sent" not in update_data:
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