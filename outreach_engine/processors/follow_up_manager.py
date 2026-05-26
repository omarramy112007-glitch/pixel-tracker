# outreach_engine/processors/follow_up_manager.py

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from outreach_engine.core.templates import TEMPLATES
from outreach_engine.core.lead_manager import get_lead
from outreach_engine.database.supabase_client import supabase


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_STEP = 1  # one automatic follow-up after the initial send
INITIAL_FOLLOWUP_DELAY_HOURS = 48

STOP_STATUSES = {
    "converted",
    "completed",
    "failed",
    "opt-out",
    "opt_out",
    "unsubscribed",
    "cancelled",
}

INITIAL_STATUSES = {"new", "pending", "not_contacted", ""}
FOLLOWUP_READY_STATUS = "sent"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _normalize(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None

    try:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

        raw = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _safe_format(text: Optional[str], context: Dict[str, Any]) -> str:
    if not text:
        return ""

    class _SafeDict(dict):
        def __missing__(self, key):
            return ""

    return str(text).format_map(_SafeDict(context))


def _template_name_for_followup_type(followup_type: str) -> str:
    """
    Fallback-safe template resolution.

    Supports either:
      - the new names: followup_no_open / followup_soft_open / interested_followup
      - older keys that may already exist in email_templates.json
    """
    fallbacks = {
        "sent": ["cold_email", "initial_outreach"],
        "followup_no_open": ["followup_no_open", "followup_1", "cold_email"],
        "followup_soft_open": ["followup_soft_open", "followup_2", "followup_1"],
        "interested_followup": ["interested_followup", "followup_3", "value_add"],
    }

    candidates = fallbacks.get(followup_type, [followup_type])
    for name in candidates:
        if name in TEMPLATES:
            return name

    return candidates[-1]


def choose_followup_type(lead: Dict[str, Any]) -> Optional[str]:
    """
    Decision logic for the single automatic follow-up.

    Rules:
      - reply_count > 0 and open_count > 0  -> interested_followup
      - open_count > 0 and reply_count == 0  -> followup_soft_open
      - open_count == 0 and reply_count == 0 -> followup_no_open

    Clicks are not part of this logic.
    """
    status = _normalize(lead.get("status"))
    if status in STOP_STATUSES:
        return None

    open_count = int(lead.get("open_count") or 0)
    reply_count = int(lead.get("reply_count") or 0)

    if reply_count > 0 and open_count > 0:
        return "interested_followup"

    if open_count > 0 and reply_count == 0:
        return "followup_soft_open"

    return "followup_no_open"


# ---------------------------------------------------------------------------
# Core decision: should we send anything now?
# ---------------------------------------------------------------------------

def determine_next_step(lead_email: str, campaign_id: int) -> int:
    """
    Returns:
      0  -> initial send is due
      1  -> one follow-up is due
      -1 -> do not send

    Only leads with status='sent' enter the follow-up path.
    """
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return -1

    status = _normalize(lead.get("status"))

    if status in STOP_STATUSES:
        return -1

    if status in INITIAL_STATUSES:
        return 0

    if status != FOLLOWUP_READY_STATUS:
        return -1

    next_followup = _parse_dt(lead.get("next_followup"))

    # Legacy fallback: compute from last_email_sent if next_followup is missing.
    if not next_followup:
        last_sent = _parse_dt(lead.get("last_email_sent"))
        if last_sent:
            next_followup = last_sent + timedelta(hours=INITIAL_FOLLOWUP_DELAY_HOURS)

    if not next_followup:
        return -1

    if _now_utc() < next_followup:
        return -1

    return 1


# ---------------------------------------------------------------------------
# Email selection / rendering metadata
# ---------------------------------------------------------------------------

def generate_next_email(
    lead_email: str,
    campaign_id: int,
    sequence_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns template metadata for the next email.

    Output:
      {
        "step": int,
        "followup_type": str,
        "template_name": str,
        "subject": str,
        "body": str,
        "html_body": str,
      }
    """
    step = determine_next_step(lead_email, campaign_id)
    if step == -1:
        return {
            "step": -1,
            "followup_type": None,
            "template_name": None,
            "subject": "",
            "body": "",
            "html_body": "",
        }

    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return {
            "step": -1,
            "followup_type": None,
            "template_name": None,
            "subject": "",
            "body": "",
            "html_body": "",
        }

    if step == 0:
        followup_type = "sent"
    else:
        followup_type = choose_followup_type(lead) or "followup_no_open"

    # Optional manual override: only use if the key exists.
    if sequence_name and sequence_name in TEMPLATES:
        template_name = sequence_name
    else:
        template_name = _template_name_for_followup_type(followup_type)

    template = TEMPLATES.get(template_name) or {}

    return {
        "step": step,
        "followup_type": followup_type,
        "template_name": template_name,
        "subject": template.get("subject", ""),
        "body": template.get("body", ""),
        "html_body": template.get("html_body", ""),
    }


# ---------------------------------------------------------------------------
# Update state after send
# ---------------------------------------------------------------------------

def update_followup(
    lead_email: str,
    campaign_id: int,
    step: int,
    status: str,
) -> None:
    """
    After the email is successfully sent, update the lead:

      - initial send => status='sent'
      - follow-up sent => status='followup_no_open' | 'followup_soft_open' | 'interested_followup'

    The status change is what removes the lead from the automatic sent queue.
    """
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return

    now = _now_iso()

    payload: Dict[str, Any] = {
        "followup_step": step,
        "status": status,
        "last_email_sent": now,
        "last_contacted": now,
        "last_updated": now,
    }

    # Initial send gets the next scheduled follow-up.
    if status == "sent":
        payload["next_followup"] = (_now_utc() + timedelta(hours=INITIAL_FOLLOWUP_DELAY_HOURS)).isoformat()
    else:
        # Once the follow-up type has been used, we stop automatic scheduling.
        payload["next_followup"] = None

    # Optional metadata trail.
    existing_metadata = lead.get("metadata")
    if isinstance(existing_metadata, dict):
        payload["metadata"] = {
            **existing_metadata,
            "last_followup_type": status,
        }

    try:
        (
            supabase.table("outreach_leads")
            .update(payload)
            .eq("email", lead_email)
            .eq("campaign_id", campaign_id)
            .execute()
        )
    except Exception as e:
        print(f"⚠️ update_followup failed → {lead_email}: {e}")
