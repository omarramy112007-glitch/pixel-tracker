# outreach_engine/processors/follow_up_manager.py

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import os

from outreach_engine.core.templates import TEMPLATES
from outreach_engine.core.lead_manager import get_lead
from outreach_engine.database.supabase_client import supabase

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FOLLOWUP_GAP_HOURS = 24

TERMINAL_STATUSES = {
    "failed",
    "replied",
    "completed",
    "converted",
    "won",
    "lost",
    "closed",
}

TERMINAL_FOLLOWUP_STATUSES = {
    "failed",
    "completed",
}

ACTION_TO_STEP = {
    "followup_no_open":   1,
    "followup_soft_open": 2,
}

ACTION_TO_FOLLOWUP_STATUS = {
    "followup_no_open":   "no_open",
    "followup_soft_open": "soft_open",
}

ACTION_TEMPLATE_CANDIDATES = {
    "followup_no_open": [
        "followup_no_open",
        "followup_1",
        "cold_email",
    ],
    "followup_soft_open": [
        "followup_soft_open",
        "followup_2",
        "followup_1",
    ],
}

_LAST_GENERATED_ACTION: Dict[Tuple[str, int], str] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _next_followup_iso() -> str:
    return (_now_utc() + timedelta(hours=FOLLOWUP_GAP_HOURS)).isoformat()


def _normalize(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


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


def _next_followup_passed(lead: Dict[str, Any]) -> bool:
    nxt = _parse_dt(lead.get("next_followup"))
    if nxt is None:
        return True
    return _now_utc() >= nxt


def _lead_key(lead_email: str, campaign_id: int) -> Tuple[str, int]:
    return (lead_email.strip().lower(), int(campaign_id))


def _store_action(lead_email: str, campaign_id: int, action: str) -> None:
    _LAST_GENERATED_ACTION[_lead_key(lead_email, campaign_id)] = action


def _get_stored_action(lead_email: str, campaign_id: int) -> Optional[str]:
    return _LAST_GENERATED_ACTION.get(_lead_key(lead_email, campaign_id))


def _render_template(
    template: Dict[str, Any], lead: Dict[str, Any]
) -> Dict[str, str]:
    first_name    = lead.get("first_name") or ""
    last_name     = lead.get("last_name") or ""
    name          = (
        lead.get("name") or f"{first_name} {last_name}"
    ).strip() or "there"
    company       = lead.get("company") or ""
    industry      = lead.get("industry") or ""
    pain_hook     = (
        lead.get("pain_points")
        or lead.get("pain_point")
        or "your current follow-up process"
    )
    dynamic_offer = lead.get("automation_maturity") or "our automation system"
    sender_name   = os.getenv("SENDER_NAME", "Your Name")
    resource_link = os.getenv("RESOURCE_LINK", "https://example.com/resource")

    context = {
        "name":          name,
        "first_name":    first_name or name,
        "last_name":     last_name,
        "company":       company,
        "industry":      industry,
        "pain_hook":     pain_hook,
        "dynamic_offer": dynamic_offer,
        "sender_name":   sender_name,
        "resource_link": resource_link,
    }

    def safe_format(text: str) -> str:
        try:
            return (text or "").format(**context)
        except Exception:
            return text or ""

    return {
        "subject":   safe_format(template.get("subject", "")),
        "body":      safe_format(template.get("body", "")),
        "html_body": safe_format(template.get("html_body", "")),
    }


def _template_for_action(action: str) -> Optional[Dict[str, Any]]:
    for name in ACTION_TEMPLATE_CANDIDATES.get(action, [action]):
        if name in TEMPLATES:
            return TEMPLATES[name]
    return None


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

def decide_followup_action(lead: Dict[str, Any]) -> Optional[str]:
    status              = _normalize(lead.get("status"))
    followup_status     = _normalize(lead.get("followup_status") or "")
    open_count          = _to_int(lead.get("open_count"))
    followup_open_count = _to_int(lead.get("followup_open_count"))
    reply_count         = _to_int(lead.get("reply_count"))

    any_open = (open_count > 0) or (followup_open_count > 0)

    if status in TERMINAL_STATUSES:
        return None

    if reply_count > 0:
        return "__mark_replied__"

    if status != "sent":
        return None

    if followup_status in TERMINAL_FOLLOWUP_STATUSES:
        return None

    if not _next_followup_passed(lead):
        return None

    if not followup_status:
        if not any_open:
            return "followup_no_open"
        return "followup_soft_open"

    elif followup_status == "no_open":
        if any_open:
            return "followup_soft_open"
        return "__mark_failed__"

    elif followup_status == "soft_open":
        return "__mark_failed__"

    return None


# ---------------------------------------------------------------------------
# DB updaters
# ---------------------------------------------------------------------------

def _update_lead_fields(
    lead_email: str, campaign_id: int, payload: Dict[str, Any]
) -> None:
    try:
        supabase.table("outreach_leads") \
            .update(payload) \
            .eq("email", lead_email) \
            .eq("campaign_id", campaign_id) \
            .execute()
    except Exception as e:
        print(f"⚠ update_lead_fields failed → {lead_email}: {e}")


def mark_lead_failed(lead_email: str, campaign_id: int) -> None:
    _update_lead_fields(lead_email, campaign_id, {
        "status":          "failed",
        "followup_status": "failed",
        "next_followup":   None,
        "last_updated":    _now_iso(),
    })
    print(f"🔴 Marked FAILED → {lead_email}")


def mark_lead_replied(lead_email: str, campaign_id: int) -> None:
    now = _now_iso()
    _update_lead_fields(lead_email, campaign_id, {
        "status":          "replied",
        "followup_status": "completed",
        "next_followup":   None,
        "replied_at":      now,
        "last_updated":    now,
    })
    print(f"✅ Marked REPLIED (status only) → {lead_email}")


def mark_lead_completed(lead_email: str, campaign_id: int) -> None:
    mark_lead_replied(lead_email, campaign_id)


def update_followup_sent(
    lead_email: str,
    campaign_id: int,
    action: Optional[str] = None,
    step: Optional[int] = None,
    thread_id: Optional[str] = None,
    gmail_message_id: Optional[str] = None,
) -> None:
    """
    FIX 11: Added sent_email_type="followup" to the payload.

    This is the field pixel_server._resolve_email_type() reads as
    Priority 2 when the URL param is absent or ambiguous.

    By writing it here — after the email is confirmed sent — the
    pixel server always knows this lead's most recent email was a
    followup, and routes the open to followup_open_count correctly.
    """
    now = _now_iso()

    resolved_action = action or _get_stored_action(lead_email, campaign_id)
    if not resolved_action:
        resolved_action = "followup_no_open"

    new_followup_status = ACTION_TO_FOLLOWUP_STATUS.get(
        resolved_action, _normalize(resolved_action)
    )

    payload: Dict[str, Any] = {
        "followup_status":       new_followup_status,
        "sent_email_type":       "followup",
        "last_followup_sent_at": now,
        "last_email_sent":       now,
        "last_contacted":        now,
        "last_updated":          now,
        "next_followup":         _next_followup_iso(),
        "followup_step":         (
            step if step is not None
            else ACTION_TO_STEP.get(resolved_action, 0)
        ),
        "status": "sent",
    }

    if thread_id:
        payload["thread_id"] = thread_id
    if gmail_message_id:
        payload["gmail_message_id"] = gmail_message_id

    try:
        res = (
            supabase.table("outreach_leads")
            .select("metadata")
            .eq("email", lead_email)
            .eq("campaign_id", campaign_id)
            .limit(1)
            .execute()
        )
        existing_meta = (res.data or [{}])[0].get("metadata") or {}
        if isinstance(existing_meta, dict):
            payload["metadata"] = {
                **existing_meta,
                "last_followup_type":   resolved_action,
                "last_followup_status": new_followup_status,
                "last_followup_at":     now,
            }
    except Exception:
        pass

    _update_lead_fields(lead_email, campaign_id, payload)

    print(
        f"📧 Follow-up state updated → {lead_email} | "
        f"action={resolved_action} | "
        f"followup_status={new_followup_status} | "
        f"sent_email_type=followup | "
        f"next_followup=+24h"
    )


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
    update_followup_sent(
        lead_email=lead_email,
        campaign_id=campaign_id,
        action=action,
        step=step,
        thread_id=thread_id,
        gmail_message_id=gmail_message_id,
    )


# ---------------------------------------------------------------------------
# Email content
# ---------------------------------------------------------------------------

def get_followup_email_content(
    action: str, lead: Dict[str, Any]
) -> Dict[str, str]:
    template = _template_for_action(action)
    if not template:
        print(f"⚠ No template found for action={action}")
        return {"subject": "", "body": "", "html_body": ""}
    return _render_template(template, lead)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_next_email(
    lead_email: str,
    campaign_id: int,
    sequence_name: str = "automation_outreach",
    step: Optional[int] = None,
    **kwargs,
) -> Dict[str, str]:
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return {"subject": "", "body": "", "html_body": "", "action": ""}

    action = decide_followup_action(lead)
    if not action:
        return {"subject": "", "body": "", "html_body": "", "action": ""}

    if action.startswith("__"):
        return {"subject": "", "body": "", "html_body": "", "action": action}

    _store_action(lead_email, campaign_id, action)

    content = get_followup_email_content(action, lead)
    if not content["subject"] and not content["body"]:
        return {"subject": "", "body": "", "html_body": "", "action": action}

    content["action"]          = action
    content["followup_status"] = ACTION_TO_FOLLOWUP_STATUS.get(action, "")
    content["step"]            = ACTION_TO_STEP.get(action, step or 0)
    content["sequence_name"]   = sequence_name

    return content


# ---------------------------------------------------------------------------
# Legacy shims
# ---------------------------------------------------------------------------

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
    if action and not action.startswith("__"):
        return action
    return None
