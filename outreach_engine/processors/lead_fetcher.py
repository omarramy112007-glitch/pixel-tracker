# outreach_engine/processors/lead_fetcher.py

import asyncio
import os
from typing import List, Dict, Optional, Any

from outreach_engine.database.supabase_client import supabase

TEST_EMAIL = os.getenv("TEST_EMAIL", "").strip().lower()


def _normalize_text(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _is_replied_or_closed(lead: Dict) -> bool:
    """
    Exclude leads that should not be re-sent.
    """
    status = _normalize_text(lead.get("status"))
    reply_status = lead.get("reply_status")
    reply_count = _to_int(lead.get("reply_count") or 0)

    if isinstance(reply_status, str):
        reply_status = reply_status.strip().lower() in {
            "1", "true", "yes", "replied", "reply", "done"
        }

    closed_statuses = {
        "replied", "converted", "won", "lost", "failed", "completed", "closed"
    }

    return (
        status in closed_statuses
        or bool(reply_status)
        or reply_count > 0
    )


def _is_initial_eligible(lead: Dict) -> bool:
    """
    Only fresh/uncontacted leads should enter initial outreach.
    """
    status = _normalize_text(lead.get("status"))
    last_email_sent = lead.get("last_email_sent")
    followup_step = _to_int(lead.get("followup_step"))

    return (
        status in {"new", "pending", "not_contacted", ""}
        and not last_email_sent
        and followup_step == 0
        and not _is_replied_or_closed(lead)
    )


def _test_mode_active(ready_leads: List[Dict]) -> bool:
    """
    Test mode is active only if TEST_EMAIL exists in the current ready leads.
    """
    if not TEST_EMAIL:
        return False

    emails = {_normalize_text(lead.get("email")) for lead in ready_leads}
    return TEST_EMAIL in emails


def normalize_lead(lead: Dict) -> Dict:
    lead_id = lead.get("id") or lead.get("lead_id") or lead.get("uuid")

    first_name = lead.get("first_name")
    last_name = lead.get("last_name")
    name = " ".join(filter(None, [first_name, last_name])) or None

    metadata = lead.get("metadata") or {}

    return {
        "id": lead_id,
        "name": name,
        "first_name": first_name,
        "last_name": last_name,
        "email": lead.get("email"),
        "company": lead.get("company"),
        "industry": lead.get("industry"),
        "lead_source": lead.get("lead_source"),
        "campaign_id": lead.get("campaign_id"),
        "country": lead.get("country"),
        "tech_stack": lead.get("tech_stack") or metadata.get("tech_stack"),
        "pain_points": lead.get("pain_points") or metadata.get("pain_points"),
        "automation_maturity": lead.get("automation_maturity") or metadata.get("automation_maturity"),
        "status": lead.get("status"),
        "reply_status": lead.get("reply_status"),
        "replied_at": lead.get("replied_at"),
        "open_count": _to_int(lead.get("open_count", 0)),
        "click_count": _to_int(lead.get("click_count", 0)),
        "reply_count": _to_int(lead.get("reply_count", 0)),
        "conversion_count": _to_int(lead.get("conversion_count", 0)),
        "last_email_sent": lead.get("last_email_sent"),
        "followup_step": _to_int(lead.get("followup_step", 0)),
        "score": lead.get("score"),
        "raw": lead,
    }


def get_ready_leads(
    min_score: float = 0,
    country: Optional[str] = None,
    tech_stack: Optional[str] = None,
    pain_point: Optional[str] = None,
    automation_maturity: Optional[str] = None,
    campaign_id: Optional[int] = None,
) -> List[Dict]:
    print("\n🚨 FETCHING LEADS DIRECTLY (FILTERED TO NEW ONLY)\n")

    query = supabase.table("outreach_leads").select("*").eq("status", "new")

    if campaign_id is not None:
        query = query.eq("campaign_id", campaign_id)

    response = query.execute()
    leads = response.data or []

    print("🧪 RAW LEADS SAMPLE:", leads[:1], "\n")

    normalized = [normalize_lead(lead) for lead in leads]

    print("🧪 NORMALIZED SAMPLE:", normalized[:1], "\n")

    for l in normalized:
        print(
            f"DEBUG → id:{l['id']} | email:{l['email']} | "
            f"status:{l['status']} | last_email_sent:{l['last_email_sent']}"
        )

    ready = [
        lead for lead in normalized
        if lead.get("email")
        and lead.get("id")
        and _is_initial_eligible(lead)
    ]

    test_mode = _test_mode_active(ready)

    if test_mode:
        ready = [
            lead for lead in ready
            if _normalize_text(lead.get("email")) == TEST_EMAIL
        ]
        print(f"\n🧪 TEST MODE ACTIVE → filtering by email: {TEST_EMAIL}\n")
    else:
        print("\n🚀 NORMAL MODE ACTIVE (no test email found)\n")

    print(f"\n✅ READY LEADS COUNT (AFTER TEST MODE CHECK): {len(ready)}\n")

    if country:
        ready = [lead for lead in ready if lead.get("country") == country]

    if tech_stack:
        ready = [
            lead for lead in ready
            if lead.get("tech_stack") and tech_stack.lower() in str(lead.get("tech_stack")).lower()
        ]

    if pain_point:
        ready = [
            lead for lead in ready
            if lead.get("pain_points") and pain_point.lower() in str(lead.get("pain_points")).lower()
        ]

    if automation_maturity:
        ready = [
            lead for lead in ready
            if lead.get("automation_maturity") == automation_maturity
        ]

    if min_score > 0:
        ready = [
            lead for lead in ready
            if (_to_int(lead.get("score")) >= min_score)
        ]

    print(f"🎯 FINAL READY COUNT: {len(ready)}\n")
    return ready


async def async_get_ready_leads(
    min_score: float = 0,
    country: Optional[str] = None,
    tech_stack: Optional[str] = None,
    pain_point: Optional[str] = None,
    automation_maturity: Optional[str] = None,
    campaign_id: Optional[int] = None,
):
    loop = asyncio.get_running_loop()

    return await loop.run_in_executor(
        None,
        lambda: get_ready_leads(
            min_score=min_score,
            country=country,
            tech_stack=tech_stack,
            pain_point=pain_point,
            automation_maturity=automation_maturity,
            campaign_id=campaign_id,
        )
    )