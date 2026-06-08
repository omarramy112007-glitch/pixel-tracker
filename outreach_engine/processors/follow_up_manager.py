# outreach_engine/processors/follow_up_manager.py

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from outreach_engine.core.templates import TEMPLATES
from outreach_engine.core.lead_manager import get_lead
from outreach_engine.database.supabase_client import supabase

FOLLOWUP_GAP_HOURS = 24

TERMINAL_STATUSES = {
    "failed", "replied", "completed", "converted",
    "won", "lost", "closed",
}

ACTION_TO_FOLLOWUP_STATUS = {
    "followup_no_open":   "no_open",
    "followup_soft_open": "soft_open",
}

ACTION_TO_STEP = {
    "followup_no_open":   1,
    "followup_soft_open": 2,
}

TEMPLATE_CANDIDATES = {
    "followup_no_open":   ["followup_no_open",   "followup_1", "cold_email"],
    "followup_soft_open": ["followup_soft_open",  "followup_2", "followup_1"],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _followup_due(lead: Dict[str, Any]) -> bool:
    nxt = _parse_dt(lead.get("next_followup"))
    if nxt is None:
        return True
    return _now() >= nxt


def _update(email: str, campaign_id: int, payload: Dict[str, Any]) -> None:
    try:
        supabase.table("outreach_leads").update(payload) \
            .eq("email", email).eq("campaign_id", campaign_id).execute()
    except Exception as e:
        print(f"⚠ DB update failed → {email}: {e}")


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

def decide_followup_action(lead: Dict[str, Any]) -> Optional[str]:
    """
    Clean state machine — no sent_email_type routing.
    Uses only DB counters and followup_status.

    open_count          = cold email opens
    followup_open_count = followup email opens
    """
    status          = _norm(lead.get("status"))
    followup_status = _norm(lead.get("followup_status") or "")
    open_count      = _to_int(lead.get("open_count"))
    reply_count     = _to_int(lead.get("reply_count"))
    fol_open_count  = _to_int(lead.get("followup_open_count"))

    if status in TERMINAL_STATUSES:
        return None
    if reply_count > 0:
        return "__mark_replied__"
    if status != "sent":
        return None
    if followup_status in {"failed", "completed"}:
        return None
    if not _followup_due(lead):
        return None

    # No followup sent yet
    if not followup_status:
        if open_count > 0:
            return "followup_soft_open"
        return "followup_no_open"

    # followup_no_open was sent
    if followup_status == "no_open":
        # opened anything (cold or followup) → send soft_open
        if open_count > 0 or fol_open_count > 0:
            return "followup_soft_open"
        return "__mark_failed__"

    # followup_soft_open was sent — no reply = dead lead
    if followup_status == "soft_open":
        return "__mark_failed__"

    return None


# ---------------------------------------------------------------------------
# DB state writers
# ---------------------------------------------------------------------------

def mark_lead_failed(email: str, campaign_id: int) -> None:
    _update(email, campaign_id, {
        "status":          "failed",
        "followup_status": "failed",
        "next_followup":   None,
        "last_updated":    _now_iso(),
    })
    print(f"🔴 Failed → {email}")


def mark_lead_replied(email: str, campaign_id: int) -> None:
    now = _now_iso()
    _update(email, campaign_id, {
        "status":          "replied",
        "followup_status": "completed",
        "next_followup":   None,
        "replied_at":      now,
        "last_updated":    now,
    })
    print(f"✅ Replied → {email}")


def mark_lead_completed(email: str, campaign_id: int) -> None:
    mark_lead_replied(email, campaign_id)


def update_followup_sent(
    lead_email: str,
    campaign_id: int,
    action: str,
    step: Optional[int] = None,
    thread_id: Optional[str] = None,
    gmail_message_id: Optional[str] = None,
) -> None:
    now                 = _now_iso()
    new_followup_status = ACTION_TO_FOLLOWUP_STATUS.get(action, action)
    new_step            = step if step is not None else ACTION_TO_STEP.get(action, 0)

    payload: Dict[str, Any] = {
        "followup_status":       new_followup_status,
        "sent_email_type":       "followup",
        "last_followup_sent_at": now,
        "last_email_sent":       now,
        "last_contacted":        now,
        "last_updated":          now,
        "next_followup":         (_now() + timedelta(hours=FOLLOWUP_GAP_HOURS)).isoformat(),
        "followup_step":         new_step,
        "status":                "sent",
    }
    if thread_id:
        payload["thread_id"] = thread_id
    if gmail_message_id:
        payload["gmail_message_id"] = gmail_message_id

    _update(lead_email, campaign_id, payload)
    print(f"📧 Followup state written → {lead_email} | {action} | {new_followup_status}")


# ---------------------------------------------------------------------------
# Email content
# ---------------------------------------------------------------------------

def _render(template: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, str]:
    name          = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip() or "there"
    sender_name   = os.getenv("SENDER_NAME", "")
    resource_link = os.getenv("RESOURCE_LINK", "https://example.com")

    ctx = {
        "name":          name,
        "first_name":    lead.get("first_name") or name,
        "company":       lead.get("company") or "",
        "industry":      lead.get("industry") or "",
        "pain_hook":     lead.get("pain_points") or "your current process",
        "dynamic_offer": lead.get("automation_maturity") or "our solution",
        "sender_name":   sender_name,
        "resource_link": resource_link,
    }

    def fmt(text: str) -> str:
        try:
            return (text or "").format(**ctx)
        except Exception:
            return text or ""

    return {
        "subject":   fmt(template.get("subject", "")),
        "body":      fmt(template.get("body", "")),
        "html_body": fmt(template.get("html_body", "")),
    }


def get_followup_email_content(action: str, lead: Dict[str, Any]) -> Dict[str, str]:
    for name in TEMPLATE_CANDIDATES.get(action, [action]):
        if name in TEMPLATES:
            return _render(TEMPLATES[name], lead)
    print(f"⚠ No template for action={action}")
    return {"subject": "", "body": "", "html_body": ""}


# ---------------------------------------------------------------------------
# Legacy shims
# ---------------------------------------------------------------------------

def generate_next_email(
    lead_email: str,
    campaign_id: int,
    **kwargs,
) -> Dict[str, str]:
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return {"subject": "", "body": "", "html_body": "", "action": ""}
    action = decide_followup_action(lead)
    if not action or action.startswith("__"):
        return {"subject": "", "body": "", "html_body": "", "action": action or ""}
    content = get_followup_email_content(action, lead)
    return {**content, "action": action}


def determine_next_step(lead_email: str, campaign_id: int) -> int:
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return -1
    action = decide_followup_action(lead)
    if action and not action.startswith("__"):
        return ACTION_TO_STEP.get(action, 1)
    return -1


def choose_followup_type(lead: Dict[str, Any]) -> Optional[str]:
    action = decide_followup_action(lead)
    return action if action and not action.startswith("__") else None


def update_followup(
    lead_email: str,
    campaign_id: int,
    action: Optional[str] = None,
    step: Optional[int] = None,
    status: str = "sent",
    thread_id: Optional[str] = None,
    gmail_message_id: Optional[str] = None,
) -> None:
    if status == "failed":
        mark_lead_failed(lead_email, campaign_id)
        return
    if status == "replied":
        mark_lead_replied(lead_email, campaign_id)
        return
    if action:
        update_followup_sent(
            lead_email, campaign_id, action, step, thread_id, gmail_message_id
        )
