# outreach_engine/processors/follow_up_manager.py

from __future__ import annotations

from typing import Dict, Optional
from datetime import datetime, timezone

from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.core.email_sequences import get_email_for_step
from outreach_engine.core.lead_manager import get_lead, update_lead_status

MAX_STEP = 4


# -----------------------------
# TIME HELPERS
# -----------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


# -----------------------------
# FOLLOWUP CLASSIFICATION
# -----------------------------

def _followup_email_type(lead: Dict) -> str:
    """
    cold + followup engagement classifier
    """
    open_count = int(lead.get("open_count") or 0)
    followup_opens = int(lead.get("followup_open_count") or 0)
    reply_count = int(lead.get("reply_count") or 0)

    if reply_count >= 1:
        return "replied"
    if open_count >= 1 or followup_opens >= 1:
        return "soft_open"
    return "no_open"


def choose_followup_type(lead: Dict) -> str:
    return _followup_email_type(lead)


# -----------------------------
# LEAD STATE UPDATES
# -----------------------------

def mark_lead_failed(lead_email: str, campaign_id: int) -> None:
    update_lead_status(
        email=lead_email,
        campaign_id=campaign_id,
        status="failed",
        metadata={
            "reason": "no_engagement_after_followup",
            "updated_at": _utcnow().isoformat(),
        },
    )
    print(f"💀 Lead marked failed → {lead_email}")


def mark_lead_replied(lead_email: str, campaign_id: int) -> None:
    update_lead_status(
        email=lead_email,
        campaign_id=campaign_id,
        status="replied",
        metadata={
            "updated_at": _utcnow().isoformat(),
        },
    )
    print(f"💬 Lead marked replied → {lead_email}")


# -----------------------------
# DECISION ENGINE (FIXED FLOW)
# -----------------------------

def decide_followup_action(lead: Dict) -> Optional[str]:
    """
    Correct flow:

    cold
      ↓
    soft_open
      ↓
    clicked?
        → followup_loom_clicked
        → else failed

    no_open
      ↓
    followup_no_open
      ↓
    failed
    """

    status = (lead.get("status") or "").lower().strip()
    reply_count = int(lead.get("reply_count") or 0)
    followup_status = (lead.get("followup_status") or "").lower().strip()
    link_clicked = bool(lead.get("link_clicked"))

    if status in {"replied", "converted", "opt-out", "failed", "completed"}:
        return None

    if reply_count >= 1:
        return "__mark_replied__"

    if status != "sent":
        return None

    followup_type = _followup_email_type(lead)

    # -------------------------
    # SOFT OPEN FLOW
    # -------------------------
    if followup_type == "soft_open":

        if followup_status == "loom_clicked":
            return "__mark_failed__"

        if followup_status == "soft_open" and link_clicked:
            return "followup_loom_clicked"

        if followup_status == "soft_open":
            return "__mark_failed__"

        return "followup_soft_open"

    # -------------------------
    # NO OPEN FLOW
    # -------------------------
    if followup_type == "no_open":

        if followup_status == "no_open":
            return "__mark_failed__"

        return "followup_no_open"

    return None


# -----------------------------
# STEP LOGIC (FIXED + SAFE)
# -----------------------------

def determine_next_step(lead_email: str, campaign_id: int) -> int:
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return 0

    status = (lead.get("status") or "").lower().strip()
    current_step = int(lead.get("followup_step") or 0)
    reply_count = int(lead.get("reply_count") or 0)
    followup_status = (lead.get("followup_status") or "").lower().strip()

    if status in {"replied", "completed", "opt-out", "unsubscribed"}:
        return -1

    if reply_count >= 1:
        return -1

    if followup_status == "loom_clicked":
        return -1

    if status in {"new", "pending", "not_contacted", ""}:
        return 0

    if current_step >= MAX_STEP:
        return -1

    return current_step + 1


# -----------------------------
# EMAIL GENERATION
# -----------------------------

def generate_next_email(
    lead_email: str,
    campaign_id: int,
    sequence_name: str = "automation_outreach",
) -> Dict[str, str]:

    step = determine_next_step(lead_email, campaign_id)
    if step == -1:
        return {"subject": "", "body": ""}

    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return {"subject": "", "body": ""}

    template_name = get_email_for_step(sequence_name, step) or "cold_email"
    followup_type = _followup_email_type(lead)

    if followup_type == "soft_open":
        subject_prefix = "Quick follow-up 🔥"
    elif followup_type == "no_open":
        subject_prefix = "Checking in ❄️"
    else:
        subject_prefix = "Following up"

    email = personalize_email(lead, step=step)
    if not email:
        return {"subject": "", "body": ""}

    email["subject"] = f"{subject_prefix} | {email.get('subject', '')}".strip(" |")
    email["followup_type"] = followup_type

    return email


# -----------------------------
# FOLLOWUP UPDATE
# -----------------------------

def update_followup(
    lead_email: str,
    campaign_id: int,
    step: int,
    status: str,
) -> None:

    timestamp = _utcnow().isoformat()

    if step == -1:
        update_lead_status(
            email=lead_email,
            campaign_id=campaign_id,
            status="completed",
            metadata={
                "followup_completed": True,
                "updated_at": timestamp,
            },
        )
        return

    update_lead_status(
        email=lead_email,
        campaign_id=campaign_id,
        followup_step=step,
        status=status,
        metadata={
            "last_email_sent_at": timestamp,
        },
    )
