# lead_engine/processing/personalization.py

from __future__ import annotations

import random
from typing import Any, Dict, Optional

from lead_engine.core.cache import get_cache, set_cache
from lead_engine.core.performance import timer
from lead_engine.core.retry import retry

PAIN_HOOKS = [
    "low demo bookings",
    "leads not converting",
    "manual follow-ups",
    "low reply rates",
    "pipeline inconsistency",
]

INVALID_PAIN_PHRASES = {
    "your challenges",
    "improve performance",
    "increase efficiency",
    "better results",
    "more revenue",
    "grow faster",
    "increase conversions",
}

TITLE_TO_PAIN = {
    "marketing": "low conversion from inbound leads",
    "growth": "low reply rates",
    "sales": "pipeline inconsistency",
    "founder": "pipeline inconsistency",
    "ceo": "pipeline inconsistency",
    "owner": "pipeline inconsistency",
    "operations": "manual follow-ups",
    "director": "lead drop-off before conversion",
}

INDUSTRY_TO_OFFER = {
    "saas": "We build automated outreach systems that increase booked calls and improve conversion rates.",
    "ecommerce": "We build automated follow-up systems that recover abandoned interest and improve conversions.",
    "agency": "We build automated outreach systems that keep your pipeline full without adding manual work.",
}

DEFAULT_DYNAMIC_OFFER = (
    "We build automated outreach systems that increase booked calls and improve conversion rates."
)


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
    return None


def clean_pain_hook(pain: Any) -> Optional[str]:
    pain_text = _first_non_empty(pain)
    if not pain_text:
        return None

    normalized = pain_text.lower().strip()
    if normalized in INVALID_PAIN_PHRASES:
        return None

    # Avoid ultra-generic phrases that still look weak
    if len(normalized) < 6:
        return None

    return pain_text


def generate_pain_hook(lead: Dict[str, Any]) -> str:
    """
    Returns a strong pain hook using:
    1) existing lead pain points if valid
    2) title-based fallback
    3) industry/title fallback pool
    """
    raw_pain = lead.get("pain_points")
    cleaned = clean_pain_hook(raw_pain)
    if cleaned:
        return cleaned

    title = (lead.get("title") or "").lower()
    industry = (lead.get("industry") or "").lower()
    company = (lead.get("company") or "").lower()

    for keyword, hook in TITLE_TO_PAIN.items():
        if keyword in title:
            return hook

    if "saas" in industry or "software" in industry or "app" in industry:
        return "low demo bookings"
    if "agency" in industry or "marketing" in industry:
        return "low reply rates"
    if "ecommerce" in industry or "retail" in industry:
        return "leads not converting"

    # Last fallback: random but still real
    return random.choice(PAIN_HOOKS)


def generate_dynamic_offer(lead: Dict[str, Any]) -> str:
    industry = (lead.get("industry") or "").lower()
    title = (lead.get("title") or "").lower()

    for keyword, offer in INDUSTRY_TO_OFFER.items():
        if keyword in industry:
            return offer

    if "marketing" in title:
        return "We help teams turn more inbound interest into qualified demos without manual follow-up."
    if "sales" in title:
        return "We help sales teams improve reply rates and keep follow-ups consistent across the pipeline."
    if "founder" in title or "ceo" in title:
        return "We help founders create a predictable pipeline with automated outreach and follow-up systems."

    return DEFAULT_DYNAMIC_OFFER


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
    cached = await get_cache(cache_key)
    if cached:
        return cached

    company = (lead.get("company") or "").strip()
    industry = (lead.get("industry") or "").strip()
    title = (lead.get("title") or "").strip()

    pain_hook = generate_pain_hook(lead)
    dynamic_offer = generate_dynamic_offer(lead)

    if company and industry:
        first_line = f"Noticed {company} is growing in {industry}."
        website_summary = f"{company} operates in {industry}."
    elif company:
        first_line = f"Noticed {company} and thought it could be a strong fit."
        website_summary = f"{company} is a company in your target segment."
    else:
        first_line = "Noticed your team and thought this could be relevant."
        website_summary = "No company summary available."

    enriched = dict(lead)
    enriched.update({
        "first_line": first_line,
        "website_summary": website_summary,
        "pain_hook": pain_hook,
        "dynamic_offer": dynamic_offer,
    })

    await set_cache(cache_key, enriched)
    return enriched