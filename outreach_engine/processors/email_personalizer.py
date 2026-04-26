# outreach_engine/processors/email_personalizer.py

from __future__ import annotations

import os
import random
from typing import Any, Dict, Optional

from outreach_engine.core.cache import get_cache, set_cache
from outreach_engine.core.templates import TEMPLATES, render_template

# ---------------------------------------------------
# ENV / BASE URLS
# ---------------------------------------------------

DEFAULT_CTA_URL = os.getenv(
    "DEFAULT_CTA_URL",
    "https://yourdomain.com/demo",
).strip().rstrip("/")

DEFAULT_CTA_TEXT = os.getenv(
    "DEFAULT_CTA_TEXT",
    "Click here to learn more."
).strip()

# ---------------------------------------------------
# HELPERS
# ---------------------------------------------------

def determine_step(lead: Dict[str, Any]) -> int:
    followups = lead.get("followup_count", 0) or 0
    opened = bool(lead.get("email_opened", False))
    clicked = bool(lead.get("link_clicked", False))

    if clicked:
        return 3
    if opened:
        return min(followups + 1, 3)
    return followups


def _first_item(value, default="low reply rates"):
    if isinstance(value, list) and value:
        item = value[0]
        if isinstance(item, str) and item.strip():
            return item.strip()

    if isinstance(value, str) and value.strip():
        return value.strip()

    return default


def _safe_format(template: str, **kwargs) -> str:
    class SafeDict(dict):
        def __missing__(self, key):
            return ""
    return template.format_map(SafeDict(**kwargs))


def _lead_id(lead: Dict[str, Any]) -> Optional[Any]:
    if lead.get("id"):
        return lead.get("id")

    raw = lead.get("raw") or {}
    if isinstance(raw, dict) and raw.get("id"):
        return raw.get("id")

    if lead.get("lead_id"):
        return lead.get("lead_id")

    return None


def _generate_pain_hook(lead: Dict[str, Any]) -> str:
    existing = lead.get("pain_hook")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()

    pain_points = lead.get("pain_points")
    if pain_points:
        return _first_item(pain_points)

    industry = (lead.get("industry") or "").lower()
    title = (lead.get("title") or "").lower()
    automation = (lead.get("automation_maturity") or "").lower()

    if "saas" in industry:
        return "low demo bookings"
    if "ecommerce" in industry or "e-commerce" in industry:
        return "low conversion rates"
    if "marketing" in industry:
        return "low reply rates"
    if "sales" in title:
        return "inconsistent follow-ups"
    if "growth" in title:
        return "pipeline inconsistency"
    if automation == "low":
        return "manual follow-ups"

    return "low reply rates"


def _build_dynamic_offer(lead: Dict[str, Any]) -> str:
    existing = lead.get("dynamic_offer")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()

    industry = (lead.get("industry") or "").lower()
    title = (lead.get("title") or "").lower()

    if "saas" in industry:
        return "automated outreach systems that increase booked calls"
    if "ecommerce" in industry or "e-commerce" in industry:
        return "follow-up systems that recover more conversions"
    if "marketing" in title:
        return "better inbound-to-demo conversion systems"
    if "sales" in title:
        return "more consistent follow-ups and higher reply rates"

    return "our solution"


def _choose_subject_template(
    template_subject: str,
    lead: Dict[str, Any],
    step: int,
    use_dynamic_subject: bool
) -> str:
    if not use_dynamic_subject:
        return template_subject

    company = lead.get("company") or ""
    pain_hook = _generate_pain_hook(lead)
    industry = (lead.get("industry") or "").lower()

    if step == 0:
        options = [
            "{pain_hook} is quietly hurting {company}",
            "Quick idea about {pain_hook} at {company}",
            "Quick idea for {company}",
            "{company} — quick thought",
            "A quick question about {company}",
        ]

        if "saas" in industry:
            options.extend([
                "A quick idea to improve {company}",
                "{pain_hook} at {company}?",
            ])

        if "ecommerce" in industry or "e-commerce" in industry:
            options.extend([
                "{pain_hook} is costing {company} conversions",
                "Quick fix idea for {company}",
            ])

        return random.choice(options)

    return template_subject


# ---------------------------------------------------
# MAIN
# ---------------------------------------------------

def personalize_email(
    lead: Dict[str, Any],
    step: int = None,
    use_dynamic_subject: bool = True
) -> Dict[str, Any]:
    """
    Returns:
        subject
        body
        html_body
        pixel_url
        tracking_link
        cta_text
        cta_url
    """
    if not lead:
        return {
            "subject": "",
            "body": "",
            "html_body": "",
            "pixel_url": "",
            "tracking_link": "",
            "cta_text": "",
            "cta_url": "",
        }

    if step is None:
        step = determine_step(lead)

    lead_id = _lead_id(lead) or lead.get("email") or "unknown"
    campaign_id = lead.get("campaign_id") or "unknown"
    cache_key = f"{lead_id}:{campaign_id}:{step}:{use_dynamic_subject}"

    cached = get_cache(cache_key)
    if cached:
        return cached

    industry = (lead.get("industry") or "").lower()

    if step == 0:
        if industry == "saas":
            template_name = "cold_email_saas"
        elif industry in {"ecommerce", "e-commerce"}:
            template_name = "cold_email_ecommerce"
        else:
            template_name = "cold_email"
    elif step == 1:
        template_name = "followup_1"
    elif step == 2:
        template_name = "followup_2"
    elif step == 3:
        template_name = "followup_3"
    else:
        template_name = "value_add"

    if template_name not in TEMPLATES:
        return {
            "subject": "",
            "body": "",
            "html_body": "",
            "pixel_url": "",
            "tracking_link": "",
            "cta_text": "",
            "cta_url": "",
        }

    pain_hook = _generate_pain_hook(lead)
    dynamic_offer = _build_dynamic_offer(lead)

    cta_text = DEFAULT_CTA_TEXT
    cta_url = (
        lead.get("resource_link")
        or lead.get("offer_link")
        or lead.get("cta_url")
        or DEFAULT_CTA_URL
    )

    result = render_template(
        template_name,
        {
            "lead_id": lead.get("id") or lead.get("lead_id"),
            "campaign_id": campaign_id,
            "name": lead.get("name")
            or f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
            or "there",
            "company": lead.get("company") or "",
            "industry": lead.get("industry") or "",
            "title": lead.get("title") or "",
            "pain_hook": pain_hook,
            "dynamic_offer": dynamic_offer,
            "sender_name": lead.get("sender_name") or "Your Name",
            "cta_text": cta_text,
            "cta_url": cta_url,
            "first_line": lead.get("first_line") or "",
            "website_summary": lead.get("website_summary") or "",
        }
    )

    subject_template = _choose_subject_template(
        result["subject"],
        lead,
        step,
        use_dynamic_subject
    )

    subject = _safe_format(
        subject_template,
        name=lead.get("name") or "there",
        company=lead.get("company") or "",
        pain_hook=pain_hook,
        dynamic_offer=dynamic_offer,
    )

    final_result = {
        **result,
        "subject": subject.strip(),
        "pain_hook": pain_hook,
        "dynamic_offer": dynamic_offer,
        "step": step,
    }

    set_cache(cache_key, final_result)
    return final_result