# File: outreach_engine/processors/email_personalizer.py

from typing import Dict, Any
from core.templates import TEMPLATES
from core.cache import get_cache, set_cache  # استخدم get/set من cache.py

def determine_step(lead: Dict[str, Any]) -> int:
    """
    Determine follow-up step automatically based on lead behavior.
    """
    followups = lead.get("followup_count", 0)
    opened = lead.get("email_opened", False)
    clicked = lead.get("link_clicked", False)

    if clicked:
        return 3  # assume last follow-up
    if opened:
        return min(followups + 1, 3)
    return followups


def personalize_email(
    lead: Dict[str, Any],
    step: int = None,  # إذا step=None، نحددها تلقائيًا
    use_dynamic_subject: bool = True
) -> Dict[str, str]:
    """
    Generate a personalized email for a lead with caching, dynamic templates, and follow-up automation.
    step: 0 = cold email, 1-3 = follow-ups, 4+ = value-add
    use_dynamic_subject: optionally customize subject based on lead pain points
    """

    # -----------------------------
    # Determine step if not provided
    # -----------------------------
    if step is None:
        step = determine_step(lead)

    # -----------------------------
    # Check cache first
    # -----------------------------
    cache_key = f"{lead.get('email')}_{step}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    # -----------------------------
    # Select template dynamically
    # -----------------------------
    industry = lead.get("industry", "").lower()
    if step == 0:
        if industry == "saas":
            template = TEMPLATES.get("cold_email_saas", TEMPLATES.get("cold_email"))
        elif industry == "ecommerce":
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

    # -----------------------------
    # Smart selection of pain & offer
    # -----------------------------
    pain_hook = lead.get("pain_points")[0] if lead.get("pain_points") else "your challenges"
    dynamic_offer = lead.get("automation_maturity") or "our solution"

    # -----------------------------
    # Optional dynamic subject line
    # -----------------------------
    subject_template = template["subject"]
    if use_dynamic_subject and lead.get("pain_points"):
        subject_template = f"{lead.get('pain_points')[0]} - {subject_template}"

    subject = subject_template.format(
        name=lead.get("name", "there"),
        company=lead.get("company", ""),
        pain_hook=pain_hook,
        dynamic_offer=dynamic_offer
    )

    body = template["body"].format(
        name=lead.get("name", "there"),
        company=lead.get("company", ""),
        pain_hook=pain_hook,
        dynamic_offer=dynamic_offer,
        sender_name="Your Name",
        resource_link="https://example.com/resource"
    )

    # -----------------------------
    # Save result to cache
    # -----------------------------
    result = {
        "subject": subject,
        "body": body
    }
    set_cache(cache_key, result)

    return result