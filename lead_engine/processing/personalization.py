# lead_engine/processing/personalization.py

import asyncio
import json

from lead_engine.core.cache import get_cache, set_cache
from lead_engine.core.retry import retry
from lead_engine.core.performance import timer

# ❌ REMOVE HEAVY MODEL COMPLETELY (FOR NOW)
# This was crashing your system


@timer("Generate Personalization")
@retry
async def generate_personalization(lead: dict) -> dict:

    if not lead:
        return lead

    cache_key = f"personalization:{lead.get('email') or lead.get('company')}"

    cached = get_cache(cache_key)
    if cached:
        return cached

    # 🔥 SIMPLE RULE-BASED (FAST + RELIABLE)
    company = lead.get("company", "")
    industry = lead.get("industry", "")
    title = lead.get("title", "")

    first_line = f"Noticed {company} is actively growing in {industry}."
    pain_hook = "inconsistent client flow"
    dynamic_offer = "We help agencies automate outreach and increase booked calls."

    lead.update({
        "first_line": first_line,
        "website_summary": f"{company} operates in {industry}.",
        "pain_hook": pain_hook,
        "dynamic_offer": dynamic_offer
    })

    set_cache(cache_key, lead)

    return lead