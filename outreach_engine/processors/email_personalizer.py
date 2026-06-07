# outreach_engine/processors/email_personalizer.py

from __future__ import annotations

import os
import random
from typing import Any, Dict, Optional

from outreach_engine.core.cache import get_cache, set_cache
from outreach_engine.core.templates import TEMPLATES, render_template

DEFAULT_CTA_URL  = os.getenv("DEFAULT_CTA_URL", "https://yourdomain.com/demo").strip().rstrip("/")
DEFAULT_CTA_TEXT = os.getenv("DEFAULT_CTA_TEXT", "Click here to learn more.").strip()


# ── Helpers ───────────────────────────────────────────────────────────────────

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
    return (
        lead.get("id")
        or (lead.get("raw") or {}).get("id")
        or lead.get("lead_id")
    )


def _generate_pain_hook(lead: Dict[str, Any]) -> str:
    existing = lead.get("pain_hook")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()

    pain_points = lead.get("pain_points")
    if pain_points:
        return _first_item(pain_points)

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


def _build_dynamic_offer(lead: Dict[str, Any]) -> str:
    existing = lead.get("dynamic_offer")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()

    industry = (lead.get("industry") or "").lower()
    title    = (lead.get("title") or "").lower()

    if "saas"      in industry:                            return "automated outreach systems that increase booked calls"
    if "ecommerce" in industry or "e-commerce" in industry: return "follow-up systems that recover more conversions"
    if "marketing" in title:                               return "better inbound-to-demo conversion systems"
    if "sales"     in title:                               return "more consistent follow-ups and higher reply rates"
    return "our solution"


def _choose_subject(
    template_subject: str,
    lead: Dict[str, Any],
    step: int,
    dynamic: bool,
) -> str:
    if not dynamic or step != 0:
        return template_subject

    company   = lead.get("company") or ""
    pain_hook = _generate_pain_hook(lead)
    industry  = (lead.get("industry") or "").lower()

    options = [
        "Quick idea about {pain_hook} at {company}",
        "Quick idea for {company}",
        "{company} — quick thought",
        "A quick question about {company}",
    ]

    if "saas" in industry:
        options += [
            "A quick idea to improve {company}",
            "{pain_hook} at {company}?",
        ]
    if "ecommerce" in industry or "e-commerce" in industry:
        options += [
            "{pain_hook} is costing {company} conversions",
            "Quick fix idea for {company}",
        ]

    return random.choice(options)


# ── Main personalizer ─────────────────────────────────────────────────────────

def personalize_email(
    lead: Dict[str, Any],
    step: int = None,
    use_dynamic_subject: bool = True,
) -> Dict[str, Any]:
    """
    Build a personalized cold/follow-up email from a template.

    step=0  → cold_email (or cold_email_saas / cold_email_ecommerce)
    step=1  → followup_1
    step=2  → followup_2
    step=3  → followup_3
    step=4+ → value_add

    Returns dict with: subject, body, html_body, pixel_url, tracking_link, cta_text, cta_url

    CRITICAL FIX: html_body is NEVER cached and NEVER returned from cache.
    html_body contains a tracking pixel URL that must be unique per send
    (different email_type, different ts). Caching it caused the cold email
    to contain a stale followup pixel URL (or vice versa), making Gmail's
    proxy fire both pixels on a single open — incrementing both open_count
    AND followup_open_count from one human action.

    subject and body (plain text) are safe to cache — they contain no
    pixel URLs. html_body is always returned as "" so that
    outreach_sender._inject_pixel() builds it fresh at send time with
    the correct email_type and ts baked in.
    """
    empty = {
        "subject":       "",
        "body":          "",
        "html_body":     "",
        "pixel_url":     "",
        "tracking_link": "",
        "cta_text":      "",
        "cta_url":       "",
    }

    if not lead:
        return empty

    if step is None:
        # Derive step from lead state — never use click as a signal
        followup_step = int(lead.get("followup_step") or 0)
        step = followup_step

    lid         = _lead_id(lead) or lead.get("email") or "unknown"
    campaign_id = lead.get("campaign_id") or "unknown"

    # Cache key for subject/body only — html_body is never cached.
    cache_key = f"{lid}:{campaign_id}:{step}:{use_dynamic_subject}:text_only"

    cached = get_cache(cache_key)
    if cached:
        # Return cached subject/body but always with html_body="" so
        # outreach_sender builds a fresh html_body with the correct pixel.
        return {**cached, "html_body": ""}

    # ── Template selection ────────────────────────────────────────────────────
    industry = (lead.get("industry") or "").lower()

    if step == 0:
        if "saas" in industry:
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

    # Fallback to initial_outreach if template missing
    if template_name not in TEMPLATES:
        template_name = "initial_outreach" if "initial_outreach" in TEMPLATES else None
    if not template_name:
        return empty

    pain_hook     = _generate_pain_hook(lead)
    dynamic_offer = _build_dynamic_offer(lead)

    cta_url = (
        lead.get("resource_link")
        or lead.get("offer_link")
        or lead.get("cta_url")
        or DEFAULT_CTA_URL
    )

    name = (
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
        "title":           lead.get("title") or (lead.get("metadata") or {}).get("title") or "",
        "pain_hook":       pain_hook,
        "dynamic_offer":   dynamic_offer,
        "sender_name":     lead.get("sender_name") or os.getenv("SENDER_NAME", "Omar Ramy"),
        "cta_text":        DEFAULT_CTA_TEXT,
        "cta_url":         cta_url,
        "first_line":      lead.get("first_line") or "",
        "website_summary": lead.get("website_summary") or "",
    })

    subject_template = _choose_subject(
        result["subject"], lead, step, use_dynamic_subject
    )
    subject = _safe_format(
        subject_template,
        name=name,
        company=lead.get("company") or "",
        pain_hook=pain_hook,
        dynamic_offer=dynamic_offer,
    )

    final = {
        **result,
        "subject":       subject.strip(),
        "pain_hook":     pain_hook,
        "dynamic_offer": dynamic_offer,
        "step":          step,
        "html_body":     "",  # never cache or return html_body — built fresh at send time
    }

    # Cache everything except html_body so it is never served stale
    # with an old pixel URL from a previous send.
    cacheable = {k: v for k, v in final.items() if k != "html_body"}
    set_cache(cache_key, cacheable)

    return final
