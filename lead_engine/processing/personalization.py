# lead_engine/processing/personalization.py

import asyncio
import json

from lead_engine.core.cache import get_cache, set_cache
from lead_engine.core.retry import retry
from lead_engine.core.performance import timer

# ❌ Heavy AI model removed for now — using fast rule-based version

@timer("Generate Personalization")
@retry
async def generate_personalization(lead: dict) -> dict:
    """
    Generate personalization fields for a lead.
    Returns lead updated with:
        - first_line
        - website_summary
        - pain_hook
        - dynamic_offer
    """
    if not lead:
        return lead

    cache_key = f"personalization:{lead.get('email') or lead.get('company')}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    # 🔥 Rule-Based Personalization
    company = lead.get("company", "")
    industry = lead.get("industry", "")
    title = lead.get("title", "")

    # Professional, polished first line
    first_line = f"Noticed {company} is actively growing in {industry}."

    # Replace generic pain hook with refined examples
    pain_hooks = [
        "insufficient product demos to fully engage potential clients",
        "high lead drop-off before conversion",
        "significant churn following trial periods",
        "suboptimal engagement from outreach campaigns",
        "irregular or unpredictable sales pipeline performance"
    ]
    # Simple selection based on title/industry (you can improve with rules)
    import random
    pain_hook = random.choice(pain_hooks)

    dynamic_offer = "We build automated outreach systems that increase booked calls and improve conversion rates."

    lead.update({
        "first_line": first_line,
        "website_summary": f"{company} operates in {industry}.",
        "pain_hook": pain_hook,
        "dynamic_offer": dynamic_offer
    })

    set_cache(cache_key, lead)
    return lead