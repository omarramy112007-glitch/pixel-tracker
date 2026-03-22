# File: outreach_engine/processors/lead_fetcher.py

from typing import List, Dict, Optional
from database.supabase_client import fetch_ready_leads
import asyncio


def get_ready_leads(
    min_score: float = 15,
    country: Optional[str] = None,
    tech_stack: Optional[str] = None,
    pain_point: Optional[str] = None,
    automation_maturity: Optional[str] = None
) -> List[Dict]:
    """
    Fetch leads ready for outreach with optional filtering.
    """

    leads = fetch_ready_leads(min_score)

    ready = [
        lead for lead in leads
        if lead.get("email")
    ]

    # Optional Filters
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