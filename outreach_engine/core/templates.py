# File: outreach_engine/core/templates.py

import json
from pathlib import Path
from typing import Dict, Any
import os

# =========================================
# Load Templates
# =========================================

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "email_templates.json"

if not TEMPLATE_PATH.exists():
    raise FileNotFoundError(f"Template file not found: {TEMPLATE_PATH}")

with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
    TEMPLATES: Dict[str, Dict[str, Any]] = json.load(f)


# =========================================
# CONFIG (FIXED: allow env override for ngrok)
# =========================================

BASE_TRACKING_URL = os.getenv(
    "BASE_TRACKING_URL",
    "http://127.0.0.1:8000"
).rstrip("/")


# =========================================
# Tracking Helpers
# =========================================

def generate_open_pixel(lead_id: int, campaign_id: int = None) -> str:
    url = f"{BASE_TRACKING_URL}/open/{lead_id}"
    if campaign_id:
        url += f"?campaign_id={campaign_id}"

    return (
        f'<img src="{url}" '
        f'width="1" height="1" '
        f'style="display:none" />'
    )


def generate_click_link(lead_id: int, url: str, campaign_id: int = None) -> str:
    tracked = f"{BASE_TRACKING_URL}/track/click?lead_id={lead_id}"

    if campaign_id:
        tracked += f"&campaign_id={campaign_id}"

    tracked += f"&url={url}"
    return tracked


# =========================================
# Template Renderer
# =========================================

def render_template(template_name: str, context: Dict[str, Any]) -> Dict[str, str]:

    if template_name not in TEMPLATES:
        raise ValueError(f"Template '{template_name}' not found.")

    template = TEMPLATES[template_name]

    subject = template.get("subject", "")
    body = template.get("body", "")

    # Replace variables
    for key, value in context.items():
        subject = subject.replace(f"{{{key}}}", str(value))
        body = body.replace(f"{{{key}}}", str(value))

    lead_id = context.get("lead_id")
    campaign_id = context.get("campaign_id")

    # =========================================
    # Click tracking injection (ONLY if placeholder exists)
    # =========================================
    if lead_id and "link" in context:
        tracked_link = generate_click_link(
            lead_id=lead_id,
            url=context["link"],
            campaign_id=campaign_id
        )
        body = body.replace("{link}", tracked_link)

    # =========================================
    # Open tracking pixel (ALWAYS last)
    # =========================================
    if lead_id:
        body += "\n" + generate_open_pixel(
            lead_id=lead_id,
            campaign_id=campaign_id
        )

    return {
        "subject": subject,
        "body": body
    }