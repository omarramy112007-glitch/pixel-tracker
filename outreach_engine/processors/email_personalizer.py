# outreach_engine/processors/email_personalizer.py

from __future__ import annotations

import os
import random
from typing import Any, Dict, Optional

from outreach_engine.core.cache import get_cache, set_cache
from outreach_engine.core.templates import TEMPLATES, render_template

DEFAULT_CTA_URL  = os.getenv("DEFAULT_CTA_URL", "https://yourdomain.com/demo").strip().rstrip("/")
DEFAULT_CTA_TEXT = os.getenv("DEFAULT_CTA_TEXT", "Click here to learn more.").strip()


def _first_item(value: Any, default: str = "low reply rates") -> str:
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
    return lead.get("id") or lead.get("lead_id")


def _pain_hook(lead: Dict[str, Any]) -> str:
    if isinstance(lead.get("pain_hook"), str) and lead["pain_hook"].strip():
        return lead["pain_hook"].strip()
    if lead.get("pain_points"):
        return _first_item(lead["pain_points"])
    industry   = (lead.get("industry") or "").lower()
    title      = (lead.get("title") or "").lower()
    automation = (lead.get("automation_maturity") or "").lower()
    if "saas"      in industry:                            return "low demo bookings"
    if "ecommerce" in industry or "e-commerce" in industry: return "low conversion rates"
    if "marketing" in industry:                            return "low reply rates"
    if "sales"     in title:                               return "inconsistent follow-ups"
    if "growth"    in title:                               return "pipeline inconsistency"
    if automation  == "low":                               return "manual follow-ups"
    return "low reply rates"


def _dynamic_offer(lead: Dict[str, Any]) -> str:
    if isinstance(lead.get("dynamic_offer"), str) and lead["dynamic_offer"].strip():
        return lead["dynamic_offer"].strip()
    industry = (lead.get("industry") or "").lower()
    title    = (lead.get("title") or "").lower()
    if "saas"      in industry:                            return "automated outreach systems that increase booked calls"
    if "ecommerce" in industry or "e-commerce" in industry: return "follow-up systems that recover more conversions"
    if "marketing" in title:                               return "better inbound-to-demo conversion systems"
    if "sales"     in title:                               return "more consistent follow-ups and higher reply rates"
    return "our solution"


def _subject(template_subject: str, lead: Dict[str, Any], step: int, dynamic: bool) -> str:
    if not dynamic or step != 0:
        return template_subject
    company  = lead.get("company") or ""
    hook     = _pain_hook(lead)
    industry = (lead.get("industry") or "").lower()
    options  = [
        "Quick idea about {pain_hook} at {company}",
        "Quick idea for {company}",
        "{company} — quick thought",
        "A quick question about {company}",
    ]
    if "saas"      in industry:
        options += ["A quick idea to improve {company}", "{pain_hook} at {company}?"]
    if "ecommerce" in industry or "e-commerce" in industry:
        options += ["{pain_hook} is costing {company} conversions", "Quick fix idea for {company}"]
    return random.choice(options)


def personalize_email(
    lead: Dict[str, Any],
    step: int = None,
    use_dynamic_subject: bool = True,
) -> Dict[str, Any]:
    """
    Returns subject and body only.
    html_body is ALWAYS returned as "" — it is built fresh at send time
    by outreach_sender._inject_pixel() with the correct pixel URL.
    This prevents any stale pixel URL from being cached and reused.
    """
    empty = {
        "subject": "", "body": "", "html_body": "",
        "pixel_url": "", "cta_url": "", "cta_text": "",
    }

    if not lead:
        return empty

    if step is None:
        step = int(lead.get("followup_step") or 0)

    lid         = _lead_id(lead) or lead.get("email") or "unknown"
    campaign_id = lead.get("campaign_id") or "unknown"
    cache_key   = f"{lid}:{campaign_id}:{step}:{use_dynamic_subject}"

    cached = get_cache(cache_key)
    if cached:
        # Never return cached html_body — always empty so pixel is fresh
        return {**cached, "html_body": ""}

    industry = (lead.get("industry") or "").lower()

    if step == 0:
        template_name = (
            "cold_email_saas"      if "saas"      in industry else
            "cold_email_ecommerce" if industry in {"ecommerce", "e-commerce"} else
            "cold_email"
        )
    elif step == 1: template_name = "followup_1"
    elif step == 2: template_name = "followup_2"
    elif step == 3: template_name = "followup_3"
    else:           template_name = "value_add"

    if template_name not in TEMPLATES:
        template_name = "initial_outreach" if "initial_outreach" in TEMPLATES else None
    if not template_name:
        return empty

    hook    = _pain_hook(lead)
    offer   = _dynamic_offer(lead)
    cta_url = lead.get("cta_url") or lead.get("resource_link") or DEFAULT_CTA_URL
    name    = (
        lead.get("name")
        or f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
        or "there"
    )

    result = render_template(template_name, {
        "lead_id":         lead.get("id") or lead.get("lead_id"),
        "campaign_id":     campaign_id,
        "name":            name,
        "company":         lead.get("company") or "",
        "industry":        lead.get("industry") or "",
        "title":           lead.get("title") or "",
        "pain_hook":       hook,
        "dynamic_offer":   offer,
        "sender_name":     lead.get("sender_name") or os.getenv("SENDER_NAME", ""),
        "cta_text":        DEFAULT_CTA_TEXT,
        "cta_url":         cta_url,
        "first_line":      lead.get("first_line") or "",
        "website_summary": lead.get("website_summary") or "",
    })

    subject_tpl = _subject(result["subject"], lead, step, use_dynamic_subject)
    subject = _safe_format(
        subject_tpl,
        name=name,
        company=lead.get("company") or "",
        pain_hook=hook,
        dynamic_offer=offer,
    )

    final = {
        **result,
        "subject":       subject.strip(),
        "html_body":     "",   # never cached, always built fresh at send time
        "pain_hook":     hook,
        "dynamic_offer": offer,
        "step":          step,
    }

    # Cache subject + body only, never html_body
    set_cache(cache_key, {k: v for k, v in final.items() if k != "html_body"})
    return final
