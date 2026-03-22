# outreach_engine/core/templates.py

import json
from pathlib import Path

TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "email_templates.json"

with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
    TEMPLATES = json.load(f)