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

# How long to wait after initial send before sending followup_no_open
NO_OPEN_DELAY_HOURS = int(48)

# How long to wait after followup_no_open before sending followup_soft_open
# (if the lead opened after the first follow-up)
SOFT_OPEN_DELAY_HOURS = int(48)

# How long to wait after followup_soft_open before marking failed
SOFT_OPEN_WAIT_HOURS = int(72)

TERMINAL_STATUSES = {
    "failed", "replied", "completed",
    "converted", "won", "lost", "closed",
}

TERMINAL_FOLLOWUP_STATUSES = {"completed", "failed"}


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
        dt  = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _hours_since(dt: Optional[datetime]) -> Optional[float]:
    if not dt:
        return None
    return (_now_utc() - dt).total_seconds() / 3600


def _template_for_action(action: str) -> Optional[str]:
    """Map action key to template name with fallbacks."""
    candidates = {
        "followup_no_open":   ["followup_no_open", "followup_1", "cold_email"],
        "followup_soft_open": ["followup_soft_open", "followup_2", "followup_1"],
    }
    for name in candidates.get(action, [action]):
        if name in TEMPLATES:
            return name
    return None


# ---------------------------------------------------------------------------
# State machine decision
# ---------------------------------------------------------------------------

def decide_followup_action(lead: Dict[str, Any]) -> Optional[str]:
    """
    Pure decision function — takes a lead dict and returns what to do next.

    Returns:
      "followup_no_open"    → send the no-open follow-up
      "followup_soft_open"  → send the soft-open follow-up
      "__mark_failed__"     → mark lead as failed, send nothing
      "__mark_completed__"  → mark lead as completed, send nothing
      None                  → do nothing

    Decision is based on status, followup_status, open_count,
    followup_open_count, and reply_count ONLY. Clicks are ignored.
    """
    status          = _normalize(lead.get("status"))
    followup_status = _normalize(lead.get("followup_status") or "")
    open_count          = int(lead.get("open_count") or 0)
    followup_open_count = int(lead.get("followup_open_count") or 0)
    reply_count         = int(lead.get("reply_count") or 0)

    # Rule 8: failed is terminal — never process
    if status == "failed":
        return None

    # Rule 7: reply at any point → stop and mark completed
    if reply_count > 0:
        return "__mark_completed__"

    # Only process leads with status='sent'
    if status != "sent":
        return None

    # Terminal followup states — don't send anything more
    if followup_status in TERMINAL_FOLLOWUP_STATUSES:
        return None

    # Timing guards
    last_email_sent       = _parse_dt(lead.get("last_email_sent"))
    last_followup_sent_at = _parse_dt(lead.get("last_followup_sent_at"))

    hours_since_email    = _hours_since(last_email_sent)
    hours_since_followup = _hours_since(last_followup_sent_at)

    # Rule 3: had no_open follow-up → still no engagement → mark failed
    if (
        (followup_status == "no_open")
        and (open_count == 0)
        and (followup_open_count == 0)
        and (reply_count == 0)
    ):
        # Must wait the full soft_open window before giving up
        if hours_since_followup is not None and hours_since_followup < SOFT_OPEN_WAIT_HOURS:
            return None  # not enough time has passed yet
        return "__mark_failed__"

    # Rule 4: had no_open follow-up → now there are opens → send soft_open
    if (
        (followup_status == "no_open")
        and ((open_count >= 1) or (followup_open_count >= 1))
        and (reply_count == 0)
    ):
        if hours_since_followup is not None and hours_since_followup < SOFT_OPEN_DELAY_HOURS:
            return None
        return "followup_soft_open"

    # Rule 5: had soft_open follow-up → still no reply → mark failed
    if (
        (followup_status == "soft_open")
        and (reply_count == 0)
    ):
        if hours_since_followup is not None and hours_since_followup < SOFT_OPEN_WAIT_HOURS:
            return None
        return "__mark_failed__"

    # Rule 6: had soft_open follow-up → reply came in → mark completed
    if (
        (followup_status == "soft_open")
        and (reply_count > 0)
    ):
        return "__mark_completed__"

    # Rule 1: no prior follow-up, no opens → send followup_no_open
    if (
        (not followup_status)
        and (open_count == 0)
        and (followup_open_count == 0)
        and (reply_count == 0)
    ):
        if hours_since_email is not None and hours_since_email < NO_OPEN_DELAY_HOURS:
            return None  # too soon
        return "followup_no_open"

    # Rule 2: no prior follow-up, has opens, no reply → send followup_soft_open
    if (
        (not followup_status)
        and (open_count >= 1)
        and (reply_count == 0)
    ):
        if hours_since_email is not None and hours_since_email < SOFT_OPEN_DELAY_HOURS:
            return None
        return "followup_soft_open"

    return None


# ---------------------------------------------------------------------------
# State updaters
# ---------------------------------------------------------------------------

def _update_lead_fields(lead_email: str, campaign_id: int, payload: Dict[str, Any]) -> None:
    try:
        supabase.table("outreach_leads").update(payload) \
            .eq("email", lead_email) \
            .eq("campaign_id", campaign_id) \
            .execute()
    except Exception as e:
        print(f"⚠️ update_lead_fields failed → {lead_email}: {e}")


def mark_lead_failed(lead_email: str, campaign_id: int) -> None:
    """Mark a lead as failed — terminal state, no more follow-ups."""
    _update_lead_fields(lead_email, campaign_id, {
        "status":          "failed",
        "followup_status": "failed",
        "next_followup":   None,
        "last_updated":    _now_iso(),
    })
    print(f"🔴 Marked FAILED → {lead_email}")


def mark_lead_completed(lead_email: str, campaign_id: int) -> None:
    """Mark a lead as completed — replied, terminal state."""
    _update_lead_fields(lead_email, campaign_id, {
        "status":          "replied",
        "followup_status": "completed",
        "next_followup":   None,
        "last_updated":    _now_iso(),
    })
    print(f"✅ Marked COMPLETED → {lead_email}")


def update_followup_sent(
    lead_email: str,
    campaign_id: int,
    action: str,
    thread_id: Optional[str] = None,
    gmail_message_id: Optional[str] = None,
) -> None:
    """
    Update lead state after a follow-up email is successfully sent.
    action is the template key: 'followup_no_open' or 'followup_soft_open'
    """
    now = _now_iso()

    # Map action to followup_status value
    followup_status_map = {
        "followup_no_open":   "no_open",
        "followup_soft_open": "soft_open",
    }
    new_followup_status = followup_status_map.get(action, action)

    payload: Dict[str, Any] = {
        "followup_status":      new_followup_status,
        "last_followup_sent_at": now,
        "last_email_sent":      now,
        "last_contacted":       now,
        "last_updated":         now,
        "next_followup":        None,  # cleared; timing is managed by decide_followup_action
    }

    if thread_id:
        payload["thread_id"] = thread_id
    if gmail_message_id:
        payload["gmail_message_id"] = gmail_message_id

    # Update metadata trail
    try:
        lead = get_lead(lead_email, campaign_id)
        if lead:
            existing_metadata = lead.get("metadata") or {}
            if isinstance(existing_metadata, dict):
                payload["metadata"] = {
                    **existing_metadata,
                    "last_followup_type": action,
                    "last_followup_at":   now,
                }
    except Exception:
        pass

    _update_lead_fields(lead_email, campaign_id, payload)
    print(f"📧 Follow-up state updated → {lead_email} | followup_status={new_followup_status}")


# ---------------------------------------------------------------------------
# Email content retrieval
# ---------------------------------------------------------------------------

def get_followup_email_content(action: str, lead: Dict[str, Any]) -> Dict[str, str]:
    """
    Returns template content for the given action.
    Falls back gracefully if template not found.
    """
    template_name = _template_for_action(action)
    if not template_name:
        print(f"⚠️ No template found for action={action}")
        return {"subject": "", "body": "", "html_body": ""}

    template = TEMPLATES.get(template_name, {})
    return {
        "subject":   template.get("subject", ""),
        "body":      template.get("body", ""),
        "html_body": template.get("html_body", ""),
    }


# ---------------------------------------------------------------------------
# Legacy compatibility shim
# ---------------------------------------------------------------------------

def determine_next_step(lead_email: str, campaign_id: int) -> int:
    """
    Legacy shim for any code still calling determine_next_step.
    Returns 1 if there is a sendable follow-up, -1 otherwise.
    """
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return -1
    action = decide_followup_action(lead)
    if action and not action.startswith("__"):
        return 1
    return -1


def choose_followup_type(lead: Dict[str, Any]) -> Optional[str]:
    """Legacy shim."""
    action = decide_followup_action(lead)
    if action and not action.startswith("__"):
        return action
    return None
