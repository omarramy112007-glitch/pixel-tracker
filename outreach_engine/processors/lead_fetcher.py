# File: outreach_engine/processors/lead_fetcher.py

from typing import List, Dict, Optional
from outreach_engine.database.supabase_client import supabase
import asyncio


def normalize_lead(lead: Dict) -> Dict:
    lead_id = lead.get("id") or lead.get("lead_id") or lead.get("uuid")

    name = " ".join(filter(None, [lead.get("first_name"), lead.get("last_name")])) or None

    metadata = lead.get("metadata") or {}

    return {
        "id": lead_id,
        "name": name,
        "email": lead.get("email"),
        "company": lead.get("company"),
        "country": lead.get("industry"),
        "tech_stack": metadata.get("tech_stack"),
        "pain_points": metadata.get("pain_points"),
        "automation_maturity": metadata.get("automation_maturity"),
        "status": lead.get("status"),
        "last_email_sent": lead.get("last_email_sent"),
        "raw": lead
    }


def get_ready_leads(
    min_score: float = 0,
    country: Optional[str] = None,
    tech_stack: Optional[str] = None,
    pain_point: Optional[str] = None,
    automation_maturity: Optional[str] = None
) -> List[Dict]:

    print("\n🚨 FETCHING LEADS DIRECTLY (NO HIDDEN FILTERS)\n")

    # 🔥 IMPORTANT: fetch ALL leads (no hidden filters)
    response = supabase.table("outreach_leads").select("*").execute()
    leads = response.data or []

    print("🧪 RAW LEADS SAMPLE:", leads[:1], "\n")

    # Normalize
    normalized = [normalize_lead(lead) for lead in leads]

    print("🧪 NORMALIZED SAMPLE:", normalized[:1], "\n")

    # 🔥 DEBUG BEFORE FILTER
    for l in normalized:
        print(f"DEBUG → id:{l['id']} | status:{l['status']} | last_email_sent:{l['last_email_sent']}")

    # ✅ ONLY minimal validation (for Bulletproof Test)
    ready = [
        lead for lead in normalized
        if lead.get("email") and lead.get("id")
    ]

    print(f"\n✅ READY LEADS COUNT (NO FILTER): {len(ready)}\n")

    # ---------------- OPTIONAL FILTERS ----------------

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

    print(f"🎯 FINAL READY COUNT: {len(ready)}\n")

    return ready


async def async_get_ready_leads(
    min_score: float = 0,  # 🔥 IMPORTANT: was 15 (this could block your lead)
    country: Optional[str] = None,
    tech_stack: Optional[str] = None,
    pain_point: Optional[str] = None,
    automation_maturity: Optional[str] = None
):

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