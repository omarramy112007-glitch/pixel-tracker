# outreach_engine/processors/email_personalizer.py

from __future__ import annotations

import random
from typing import Any, Dict

from outreach_engine.core.cache import get_cache, set_cache
from outreach_engine.core.templates import TEMPLATES


def determine_step(lead: Dict[str, Any]) -> int:
    """
    Determine follow-up step automatically based on lead behavior.
    """
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
    """
    Safe formatter: leaves unknown placeholders empty instead of crashing.
    """
    class SafeDict(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(SafeDict(**kwargs))


def _generate_pain_hook(lead: Dict[str, Any]) -> str:
    """
    Always returns a human-sounding pain hook.
    Never falls back to generic placeholders.
    """
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
    """
    Returns the subject template to format.
    For step 0, we rotate subject styles.
    """
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
            "A quick question about {company}"
        ]

        if "saas" in industry:
            options.extend([
                "A quick idea to improve {company}",
                "{pain_hook} at {company}?"
            ])

        if "ecommerce" in industry or "e-commerce" in industry:
            options.extend([
                "{pain_hook} is costing {company} conversions",
                "Quick fix idea for {company}"
            ])

        return random.choice(options)

    return template_subject


def personalize_email(
    lead: Dict[str, Any],
    step: int = None,
    use_dynamic_subject: bool = True
) -> Dict[str, str]:
    """
    Generate a personalized email for a lead with caching,
    dynamic templates, and follow-up automation.
    """
    if not lead:
        return {"subject": "", "body": ""}

    if step is None:
        step = determine_step(lead)

    cache_key = f"{lead.get('email')}_{step}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    industry = (lead.get("industry") or "").lower()

    if step == 0:
        if industry == "saas":
            template = TEMPLATES.get("cold_email_saas", TEMPLATES.get("cold_email", {}))
        elif industry in {"ecommerce", "e-commerce"}:
            template = TEMPLATES.get("cold_email_ecommerce", TEMPLATES.get("cold_email", {}))
        else:
            template = TEMPLATES.get("cold_email", {})
    elif step == 1:
        template = TEMPLATES.get("followup_1", {})
    elif step == 2:
        template = TEMPLATES.get("followup_2", {})
    elif step == 3:
        template = TEMPLATES.get("followup_3", {})
    else:
        template = TEMPLATES.get("value_add", {})

    if not template:
        return {"subject": "", "body": ""}

    pain_hook = _generate_pain_hook(lead)
    dynamic_offer = _build_dynamic_offer(lead)

    raw_subject = template.get("subject", "")
    subject_template = _choose_subject_template(raw_subject, lead, step, use_dynamic_subject)

    subject = _safe_format(
        subject_template,
        name=lead.get("name") or "there",
        company=lead.get("company") or "",
        pain_hook=pain_hook,
        dynamic_offer=dynamic_offer,
    )

    body = _safe_format(
        template.get("body", ""),
        name=lead.get("name") or "there",
        company=lead.get("company") or "",
        pain_hook=pain_hook,
        dynamic_offer=dynamic_offer,
        sender_name=lead.get("sender_name") or "Your Name",
        resource_link=lead.get("resource_link") or "https://example.com/resource",
        first_line=lead.get("first_line") or "",
        website_summary=lead.get("website_summary") or "",
    )

    result = {
        "subject": subject.strip(),
        "body": body.strip(),
        "pain_hook": pain_hook,
        "dynamic_offer": dynamic_offer,
        "step": step,
    }

    set_cache(cache_key, result)
    return result