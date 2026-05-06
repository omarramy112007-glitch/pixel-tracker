# outreach_engine/core/templates.py

from __future__ import annotations

import json
import os
import re
import time
from html import escape as html_escape
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote_plus, urlencode

# =========================================
# Load Templates
# =========================================

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "email_templates.json"

if not TEMPLATE_PATH.exists():
    raise FileNotFoundError(f"Template file not found: {TEMPLATE_PATH}")

with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
    TEMPLATES: Dict[str, Dict[str, Any]] = json.load(f)

# =========================================
# CONFIG
# =========================================

DEFAULT_PUBLIC_TRACKING_URL = "https://pixel-tracker-production.up.railway.app"

BASE_TRACKING_URL = (
    os.getenv("BASE_TRACKING_URL")
    or os.getenv("PUBLIC_TRACKING_BASE_URL")
    or os.getenv("TRACKING_BASE_URL")
    or DEFAULT_PUBLIC_TRACKING_URL
).rstrip("/")

BASE_CLICK_URL = (
    os.getenv("BASE_CLICK_URL")
    or os.getenv("CLICK_TRACK_BASE_URL")
    or os.getenv("CLICK_TRACKING_BASE_URL")
    or BASE_TRACKING_URL
).rstrip("/")

VISIBLE_CTA_URL = os.getenv(
    "VISIBLE_CTA_URL",
    "https://yourdomain.com/demo",
).rstrip("/")

DEFAULT_CTA_TEXT = os.getenv(
    "DEFAULT_CTA_TEXT",
    "Click here to learn more."
).strip()

# =========================================
# Tracking Helpers
# =========================================

def generate_open_pixel(
    lead_id: int,
    campaign_id: Optional[int] = None,
    cache_buster: Optional[int] = None,
) -> str:
    if lead_id is None:
        return ""

    ts = cache_buster or int(time.time())

    params = {"t": str(ts)}
    if campaign_id is not None:
        params["campaign_id"] = str(campaign_id)

    query = urlencode(params)
    url = f"{BASE_TRACKING_URL}/open/{lead_id}?{query}"

    return (
        f'<img src="{url}" '
        f'width="1" height="1" '
        f'style="display:none;opacity:0;mso-hide:all;visibility:hidden;" '
        f'alt="" />'
    )


def generate_click_link(lead_id: int, url: str, campaign_id: Optional[int] = None) -> str:
    if lead_id is None:
        return ""

    params = {}

    if campaign_id is not None:
        params["campaign_id"] = str(campaign_id)

    if url:
        params["url"] = url

    query = urlencode(params, quote_via=quote_plus)
    return f"{BASE_CLICK_URL}/click/{lead_id}" + (f"?{query}" if query else "")

# =========================================
# HTML Builder
# =========================================

def _build_html_email(
    text_body: str,
    cta_text: str = "",
    cta_href: str = "",
    pixel: str = ""
) -> str:
    """
    Convert clean text into HTML.
    CTA becomes a clickable anchor.
    Pixel is appended invisibly.
    """
    safe_text = html_escape(text_body or "").replace("\n", "<br>\n")

    if cta_text and cta_href:
        escaped_cta = html_escape(cta_text)
        escaped_href = html_escape(cta_href, quote=True)
        anchor = (
            f'<a href="{escaped_href}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#2563eb;text-decoration:underline;font-weight:600;">'
            f'{escaped_cta}</a>'
        )
        # Replace the first visible occurrence only
        safe_text = safe_text.replace(escaped_cta, anchor, 1)

    return f"""
<html>
  <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #111827; background: #ffffff;">
    <div style="white-space: normal;">
      {safe_text}
    </div>
    {pixel}
  </body>
</html>
""".strip()

# =========================================
# Template Renderer
# =========================================

class SafeDict(dict):
    def __missing__(self, key):
        return ""


def render_template(template_name: str, context: Dict[str, Any]) -> Dict[str, str]:
    if template_name not in TEMPLATES:
        raise ValueError(f"Template '{template_name}' not found.")

    template = TEMPLATES[template_name]
    subject = template.get("subject", "")
    body = template.get("body", "")
    html_body_template = template.get("html_body", "")

    cta_text = str(context.get("cta_text") or DEFAULT_CTA_TEXT).strip()
    cta_url = str(context.get("cta_url") or VISIBLE_CTA_URL).strip().rstrip("/")
    lead_id = context.get("lead_id")
    campaign_id = context.get("campaign_id")

    render_context = SafeDict(dict(context))
    defaults = {
        "cta_text": cta_text,
        "cta_block": cta_text,
        "cta_url": cta_url,
        "visible_cta_url": cta_url,
        "sender_name": context.get("sender_name") or "Your Name",
        "resource_link": cta_text,
        "link": cta_text,
        "tracking_link": cta_text,
        "first_line": context.get("first_line") or "",
        "website_summary": context.get("website_summary") or "",
        "pain_hook": context.get("pain_hook") or "",
        "dynamic_offer": context.get("dynamic_offer") or "",
        "name": context.get("name") or "there",
        "company": context.get("company") or "",
        "industry": context.get("industry") or "",
        "title": context.get("title") or "",
    }
    for key, value in defaults.items():
        render_context.setdefault(key, value)

    subject = subject.format_map(render_context)
    body = body.format_map(render_context)

    # Remove localhost / 127.0.0.1 from any outbound copy
    for bad in (
        "http://localhost",
        "https://localhost",
        "http://127.0.0.1",
        "https://127.0.0.1",
    ):
        subject = subject.replace(bad, "")
        body = body.replace(bad, "")

    # Clean visible text body; HTML will get the clickable CTA separately
    body = re.sub(r"https?://\S+", "", body).replace("  ", " ").strip()

    pixel = ""
    if lead_id is not None:
        pixel = generate_open_pixel(
            lead_id=int(lead_id),
            campaign_id=int(campaign_id) if campaign_id is not None else None,
            cache_buster=int(time.time()),
        )

    tracking_link = ""
    if lead_id is not None:
        tracking_link = generate_click_link(
            lead_id=int(lead_id),
            url=cta_url,
            campaign_id=int(campaign_id) if campaign_id is not None else None,
        )

    # Build HTML body
    if html_body_template:
        html_render_context = SafeDict(dict(render_context))
        html_render_context["tracking_link"] = tracking_link or cta_url
        html_render_context["pixel_tag"] = pixel
        html_render_context["cta_text"] = cta_text
        html_render_context["cta_url"] = tracking_link or cta_url

        html_body = html_body_template.format_map(html_render_context)
        html_body = html_body.replace("{pixel_tag}", pixel)
    else:
        html_body = _build_html_email(
            text_body=body,
            cta_text=cta_text,
            cta_href=tracking_link or cta_url,
            pixel=pixel,
        )

    # Final cleanup
    for bad in (
        "http://localhost",
        "https://localhost",
        "http://127.0.0.1",
        "https://127.0.0.1",
    ):
        html_body = html_body.replace(bad, "")

    return {
        "subject": subject.strip(),
        "body": body.strip(),
        "text_body": body.strip(),
        "html_body": html_body,
        "tracking_link": tracking_link,
        "pixel_url": f"{BASE_TRACKING_URL}/open/{lead_id}" if lead_id is not None else "",
        "cta_text": cta_text,
        "cta_url": cta_url,
    }