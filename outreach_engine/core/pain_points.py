# outreach_engine/core/pain_points.py

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

PAIN_POINTS_PATH = (
    Path(__file__).resolve().parent.parent / "templates" / "pain_points.json"
)

if not PAIN_POINTS_PATH.exists():
    raise FileNotFoundError(f"Pain points file not found: {PAIN_POINTS_PATH}")

with open(PAIN_POINTS_PATH, "r", encoding="utf-8") as f:
    PAIN_POINTS: Dict[str, Dict[str, str]] = json.load(f)

DEFAULT_PAIN_POINT_KEY = "lost_leads"

REQUIRED_FIELDS = (
    "pain_hook",
    "pain_stat",
    "dollar_frame",
    "automation_one_liner",
)


def _is_valid_pain_object(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    return all(field in obj for field in REQUIRED_FIELDS)


def get_pain_point(key: Optional[str]) -> Dict[str, str]:
    """
    Look up a pain point by key.

    Fallback order:
      1. The requested key, if it exists and has all required fields.
      2. DEFAULT_PAIN_POINT_KEY ("lost_leads"), if the requested key is
         missing, empty, unset, or malformed.

    This always returns a usable dict with all 4 required fields
    populated, PLUS loom_url (empty string if that pain has no video
    yet) — callers never need to handle a None, partial, or missing
    loom_url result.
    """
    clean_key = (key or "").strip().lower()

    if clean_key and clean_key in PAIN_POINTS and _is_valid_pain_object(PAIN_POINTS[clean_key]):
        result = dict(PAIN_POINTS[clean_key])
        result.setdefault("loom_url", "")
        return result

    fallback = PAIN_POINTS.get(DEFAULT_PAIN_POINT_KEY)
    if fallback and _is_valid_pain_object(fallback):
        result = dict(fallback)
        result.setdefault("loom_url", "")
        return result

    # Last-resort hardcoded fallback in case the JSON file itself is broken —
    # this should never trigger in normal operation, but guarantees the
    # email payload builder never crashes on a missing pain point.
    return {
        "pain_hook": "slow internal processes",
        "pain_stat": "manual, repetitive tasks quietly eat hours every week",
        "dollar_frame": "that adds up to real, measurable cost over a year",
        "automation_one_liner": "a simple automation that removes the manual step entirely",
        "loom_url": "",
    }


def list_pain_point_keys() -> list:
    """Returns all valid pain point keys — useful for campaign setup / validation."""
    return [k for k, v in PAIN_POINTS.items() if _is_valid_pain_object(v)]

def has_loom_video(key: Optional[str]) -> bool:
    """True only if this pain point has a real, non-empty loom_url."""
    return bool(get_pain_point(key).get("loom_url", "").strip())


def list_pain_points_missing_video() -> list:
    """Returns pain point keys that don't have a loom_url set yet."""
    return [
        k for k in list_pain_point_keys()
        if not PAIN_POINTS[k].get("loom_url", "").strip()
    ]
