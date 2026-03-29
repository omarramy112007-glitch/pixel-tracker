# File: outreach_engine/processors/lead_fetcher.py

from typing import List, Dict, Optional
from outreach_engine.database.supabase_client import fetch_ready_leads
import asyncio


def normalize_lead(lead: Dict) -> Dict:
    """
    Normalize lead structure to ensure required fields exist.
    """

    # 🔥 Try multiple possible ID keys (VERY IMPORTANT)
    lead_id = (
        lead.get("id")
        or lead.get("lead_id")
        or lead.get("uuid")
    )

    return {
        "id": lead_id,  # ✅ FIXED
        "name": lead.get("person_name") or lead.get("name"),
        "email": lead.get("email"),
        "company": lead.get("company"),
        "country": lead.get("country"),
        "tech_stack": lead.get("tech_stack"),
        "pain_points": lead.get("pain_signals") or lead.get("pain_points"),
        "automation_maturity": lead.get("automation_maturity"),
        "raw": lead  # keep original for debugging
    }


def get_ready_leads(
    min_score: float = 0,
    country: Optional[str] = None,
    tech_stack: Optional[str] = None,
    pain_point: Optional[str] = None,
    automation_maturity: Optional[str] = None
) -> List[Dict]:
    """
    Fetch leads ready for outreach with optional filtering.
    """

    leads = fetch_ready_leads(min_score)

    # 🧪 DEBUG RAW
    print("\n🧪 RAW LEADS SAMPLE:", leads[:1], "\n")

    # 🔥 Normalize
    normalized = [normalize_lead(lead) for lead in leads]

    # 🧪 DEBUG NORMALIZED
    print("🧪 NORMALIZED SAMPLE:", normalized[:1], "\n")

    # 🔥 FILTER ONLY VALID LEADS
    ready = [
        lead for lead in normalized
        if lead.get("email") and lead.get("id")
    ]

    # 🧪 DEBUG FINAL
    print(f"✅ READY LEADS COUNT: {len(ready)}\n")

    # ---------------- Filters ----------------

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

    return ready


async def async_get_ready_leads(
    min_score: float = 15,
    country: Optional[str] = None,
    tech_stack: Optional[str] = None,
    pain_point: Optional[str] = None,
    automation_maturity: Optional[str] = None
):
    """
    Async wrapper for fetching leads.
    """

    loop = asyncio.get_running_loop()

    return await loop.run_in_executor(
        None,
        get_ready_leads,
        min_score,
        country,
        tech_stack,
        pain_point,
        automation_maturity
    )