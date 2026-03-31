# File: outreach_engine/processors/email_personalizer.py

from typing import Dict, Any
from outreach_engine.core.templates import TEMPLATES
from outreach_engine.core.cache import get_cache, set_cache


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


def _first_item(value, default="your challenges"):
    if isinstance(value, list) and value:
        return value[0]
    if isinstance(value, str) and value.strip():
        return value
    return default


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
            template = TEMPLATES.get("cold_email_saas", TEMPLATES.get("cold_email"))
        elif industry in {"ecommerce", "e-commerce"}:
            template = TEMPLATES.get("cold_email_ecommerce", TEMPLATES.get("cold_email"))
        else:
            template = TEMPLATES.get("cold_email")
    elif step == 1:
        template = TEMPLATES.get("followup_1")
    elif step == 2:
        template = TEMPLATES.get("followup_2")
    elif step == 3:
        template = TEMPLATES.get("followup_3")
    else:
        template = TEMPLATES.get("value_add")

    if not template:
        return {"subject": "", "body": ""}

    pain_hook = _first_item(lead.get("pain_points"), "your challenges")
    dynamic_offer = lead.get("automation_maturity") or "our solution"

    subject_template = template.get("subject", "")
    if use_dynamic_subject and lead.get("pain_points"):
        subject_template = f"{pain_hook} - {subject_template}"

    subject = subject_template.format(
        name=lead.get("name") or "there",
        company=lead.get("company") or "",
        pain_hook=pain_hook,
        dynamic_offer=dynamic_offer
    )

    body = template.get("body", "").format(
        name=lead.get("name") or "there",
        company=lead.get("company") or "",
        pain_hook=pain_hook,
        dynamic_offer=dynamic_offer,
        sender_name="Your Name",
        resource_link="https://example.com/resource"
    )

    result = {
        "subject": subject,
        "body": body
    }

    set_cache(cache_key, result)
    return result