# outreach_engine/core/templates.py

import json
from pathlib import Path

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "email_templates.json"

if not TEMPLATE_PATH.exists():
    raise FileNotFoundError(f"Template file not found: {TEMPLATE_PATH}")

with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
    TEMPLATES = json.load(f)