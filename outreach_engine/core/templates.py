# File: outreach_engine/core/templates.py

import json
from pathlib import Path
from typing import Dict, Any

# =========================================
# Load Templates
# =========================================

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "email_templates.json"

if not TEMPLATE_PATH.exists():
    raise FileNotFoundError(f"Template file not found: {TEMPLATE_PATH}")

with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
    TEMPLATES: Dict[str, Dict[str, Any]] = json.load(f)


# =========================================
# CONFIG (CHANGE THIS TO YOUR NGROK URL)
# =========================================

BASE_TRACKING_URL = "https://regena-nonreproductive-vernia.ngrok-free.dev"


# =========================================
# Tracking Helpers
# =========================================

def generate_open_pixel(lead_id: int) -> str:
    return f'<img src="{BASE_TRACKING_URL}/open/{lead_id}" width="1" height="1" style="display:none;" />'


def generate_click_link(lead_id: int, url: str) -> str:
    return f"{BASE_TRACKING_URL}/track/click?lead_id={lead_id}&url={url}"


# =========================================
# Template Renderer
# =========================================

def render_template(template_name: str, context: Dict[str, Any]) -> Dict[str, str]:
    """
    Renders email template with tracking links and pixel.
    """

    if template_name not in TEMPLATES:
        raise ValueError(f"Template '{template_name}' not found.")

    template = TEMPLATES[template_name]

    subject = template.get("subject", "")
    body = template.get("body", "")

    # Replace variables in subject/body
    for key, value in context.items():
        subject = subject.replace(f"{{{key}}}", str(value))
        body = body.replace(f"{{{key}}}", str(value))

    lead_id = context.get("lead_id")

    # =========================================
    # Inject Click Tracking (replace any raw links)
    # =========================================
    if "link" in context:
        tracked_link = generate_click_link(lead_id, context["link"])
        body = body.replace("{link}", tracked_link)

    # =========================================
    # Inject Open Tracking Pixel
    # =========================================
    if lead_id:
        body += "\n\n" + generate_open_pixel(lead_id)

    return {
        "subject": subject,
        "body": body
    }