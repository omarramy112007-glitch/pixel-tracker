from __future__ import annotations

import json
import os
import re
from html import escape as html_escape
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "email_templates.json"

if not TEMPLATE_PATH.exists():
    raise FileNotFoundError(f"Template file not found: {TEMPLATE_PATH}")

with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
    TEMPLATES: Dict[str, Dict[str, Any]] = json.load(f)

BASE_TRACKING_URL = os.getenv(
    "BASE_TRACKING_URL",
    "https://regena-nonreproductive-vernia.ngrok-free.dev",
).rstrip("/")

BASE_CLICK_URL = os.getenv(
    "BASE_CLICK_URL",
    "https://regena-nonreproductive-vernia.ngrok-free.dev",
).rstrip("/")

VISIBLE_CTA_URL = os.getenv(
    "VISIBLE_CTA_URL",
    "https://yourdomain.com/demo",
).rstrip("/")

DEFAULT_CTA_TEXT = os.getenv(
    "DEFAULT_CTA_TEXT",
    "Click here to learn more.",
).strip()


def generate_open_pixel(lead_id: int, campaign_id: Optional[int] = None) -> str:
    """
    Returns empty string — pixel injection is handled exclusively
    by outreach_sender._build_tracking_urls() which embeds the correct
    email_type in the URL. Injecting a second pixel here would create
    a typeless hit that always increments open_count regardless of
    which email was actually opened.
    """
    return ""


def generate_click_link(
    lead_id: int,
    url: str,
    campaign_id: Optional[int] = None,
) -> str:
    tracked = f"{BASE_CLICK_URL}/click/{lead_id}"
    params  = []
    if campaign_id is not None:
        params.append(("campaign_id", str(campaign_id)))
    if url:
        params.append(("url", url))
    if params:
        query    = "&".join(f"{k}={quote_plus(v)}" for k, v in params)
        tracked += "?" + query
    return tracked


def _build_html_email(
    text_body: str,
    cta_text:  str = "",
    cta_href:  str = "",
    pixel:     str = "",
) -> str:
    """
    Build HTML email body.
    pixel parameter is kept for API compatibility but always empty —
    pixel is already embedded in html_body by outreach_sender before
    this function is called via _render_template / context pixel_tag.
    """
    safe_text = html_escape(text_body or "").replace("\n", "<br>\n")

    if cta_text and cta_href:
        escaped_cta  = html_escape(cta_text)
        escaped_href = html_escape(cta_href, quote=True)
        anchor = (
            f'<a href="{escaped_href}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#2563eb;text-decoration:underline;font-weight:600;">'
            f"{escaped_cta}</a>"
        )
        safe_text = safe_text.replace(escaped_cta, anchor, 1)

    # pixel arg intentionally not rendered — see docstring above
    return f"""<html>
  <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #111827; background: #ffffff;">
    <div style="white-space: normal;">
      {safe_text}
    </div>
  </body>
</html>""".strip()


class SafeDict(dict):
    def __missing__(self, key):
        return ""


def render_template(
    template_name: str,
    context:       Dict[str, Any],
) -> Dict[str, str]:
    if template_name not in TEMPLATES:
        raise ValueError(f"Template '{template_name}' not found.")

    template = TEMPLATES[template_name]
    subject  = template.get("subject", "")
    body     = template.get("body", "")

    cta_text    = str(context.get("cta_text")   or DEFAULT_CTA_TEXT).strip()
    cta_url     = str(context.get("cta_url")    or VISIBLE_CTA_URL).strip().rstrip("/")
    lead_id     = context.get("lead_id")
    campaign_id = context.get("campaign_id")

    render_context = SafeDict(dict(context))
    defaults = {
        "cta_text":        cta_text,
        "cta_block":       cta_text,
        "cta_url":         cta_url,
        "visible_cta_url": cta_url,
        "sender_name":     context.get("sender_name") or "Your Name",
        "resource_link":   cta_text,
        "link":            cta_text,
        "tracking_link":   cta_text,
        "first_line":      context.get("first_line")      or "",
        "website_summary": context.get("website_summary") or "",
        "pain_hook":       context.get("pain_hook")       or "",
        "dynamic_offer":   context.get("dynamic_offer")   or "",
        "name":            context.get("name")            or "there",
        "company":         context.get("company")         or "",
        "industry":        context.get("industry")        or "",
        "title":           context.get("title")           or "",
    }
    for key, value in defaults.items():
        render_context.setdefault(key, value)

    subject = subject.format_map(render_context)
    body    = body.format_map(render_context)

    for bad in (
        "http://localhost", "https://localhost",
        "http://127.0.0.1", "https://127.0.0.1",
    ):
        subject = subject.replace(bad, "")
        body    = body.replace(bad, "")

    body = re.sub(r"https?://\S+", "", body).replace("  ", " ").strip()

    tracking_link = ""
    if lead_id is not None:
        tracking_link = generate_click_link(
            lead_id=lead_id,
            url=cta_url,
            campaign_id=campaign_id,
        )

    # No pixel injected here — outreach_sender puts pixel_tag in context
    # and the template's {pixel_tag} placeholder renders it directly
    html_body = _build_html_email(
        text_body=body,
        cta_text=cta_text,
        cta_href=tracking_link or cta_url,
        pixel="",
    )

    return {
        "subject":       subject.strip(),
        "body":          body.strip(),
        "text_body":     body.strip(),
        "html_body":     html_body,
        "tracking_link": tracking_link,
        "pixel_url":     (
            f"{BASE_TRACKING_URL}/open/{lead_id}"
            if lead_id is not None else ""
        ),
        "cta_text":      cta_text,
        "cta_url":       cta_url,
    }
