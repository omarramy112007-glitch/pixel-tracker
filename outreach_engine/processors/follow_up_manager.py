# outreach_engine/processors/follow_up_manager.py

from __future__ import annotations

from typing import Dict, Optional
from datetime import datetime, timezone

from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.core.email_sequences import get_email_for_step
from outreach_engine.core.lead_manager import get_lead, update_lead_status
from outreach_engine.analytics.lead_scoring import calculate_engagement_score

MAX_STEP                  = 4
LOW_ENGAGEMENT_THRESHOLD  = 1
HIGH_ENGAGEMENT_THRESHOLD = 4

FOLLOWUP_DELAYS = {0: 0, 1: 2, 2: 3, 3: 4, 4: 5}  # days between steps


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


def _followup_email_type(lead: Dict) -> str:
    """
    FIX: Use followup_open_count — NOT open_count — to decide followup type.

    open_count          = cold email opens  (pixel: email_type=cold)
    followup_open_count = followup opens    (pixel: email_type=followup)

    The old code used open_count here, which caused two problems:
      1. A cold email open (open_count=1) would make this return 'soft_open'
         before any followup was ever sent — wrong template, wrong tone.
      2. A followup open (followup_open_count=1) was invisible to this
         function, so it kept returning 'no_open' even after the followup
         was opened.
    """
    followup_opens = int(lead.get("followup_open_count") or 0)
    reply_count    = int(lead.get("reply_count") or 0)

    if reply_count >= 1:
        return "replied"
    if followup_opens >= 1:
        return "soft_open"
    return "no_open"


def choose_followup_type(lead: Dict) -> str:
    """
    Public alias for _followup_email_type.
    Returns: 'no_open' | 'soft_open' | 'replied'
    """
    return _followup_email_type(lead)


def mark_lead_failed(lead_email: str, campaign_id: int) -> None:
    """Mark a lead as failed and stop outreach."""
    update_lead_status(
        email=lead_email,
        campaign_id=campaign_id,
        status="failed",
        metadata={"reason": "no_engagement_after_followup",
                  "updated_at": datetime.utcnow().isoformat()},
    )
    print(f"💀 Lead marked failed → {lead_email}")


def mark_lead_replied(lead_email: str, campaign_id: int) -> None:
    """Mark a lead as replied and stop outreach."""
    update_lead_status(
        email=lead_email,
        campaign_id=campaign_id,
        status="replied",
        metadata={"updated_at": datetime.utcnow().isoformat()},
    )
    print(f"💬 Lead marked replied → {lead_email}")


def decide_followup_action(lead: Dict) -> Optional[str]:
    """
    Decide what action to take for a follow-up lead.

    Returns:
      None                  → not due yet / skip
      '__mark_failed__'     → lead is dead, mark failed
      '__mark_replied__'    → lead replied, close it out
      'followup_no_open'    → send no-open follow-up
      'followup_soft_open'  → send soft-open follow-up
      'interested_followup' → lead is warm (blocked upstream)

    FIX: Uses followup_open_count (not open_count) via _followup_email_type.
    """
    status          = (lead.get("status") or "").lower().strip()
    reply_count     = int(lead.get("reply_count") or 0)
    current_step    = int(lead.get("followup_step") or 0)
    followup_status = (lead.get("followup_status") or "").lower().strip()

    # Already terminal
    if status in {"replied", "converted", "opt-out", "failed", "completed"}:
        return None

    # Reply detected in counts but status not updated yet
    if reply_count >= 1:
        return "__mark_replied__"

    # Must be in 'sent' state to be eligible for follow-up
    if status != "sent":
        return None

    # Max steps reached with no engagement → kill it
    if current_step >= MAX_STEP:
        return "__mark_failed__"

    # Check if it's time based on last_email_sent + delay for current step
    last_sent = _parse_dt(lead.get("last_email_sent"))
    if last_sent:
        delay_days = FOLLOWUP_DELAYS.get(current_step, 2)
        due_at     = last_sent.replace(tzinfo=timezone.utc) \
                     if not last_sent.tzinfo else last_sent
        due_at     = due_at + __import__("datetime").timedelta(days=delay_days)
        if _utcnow() < due_at:
            return None  # not due yet

    # Decide which follow-up type to send
    # _followup_email_type now correctly reads followup_open_count
    followup_type = _followup_email_type(lead)

    if followup_type == "replied":
        return "__mark_replied__"

    if followup_type == "soft_open":
        # Already sent a soft_open follow-up and still no reply → mark failed
        if followup_status == "soft_open":
            return "__mark_failed__"
        return "followup_soft_open"

    # no_open path
    if followup_status == "no_open":
        # Already sent a no_open follow-up and still no open → mark failed
        return "__mark_failed__"

    return "followup_no_open"


def determine_next_step(lead_email: str, campaign_id: int) -> int:
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return 0

    status          = (lead.get("status") or "").lower().strip()
    current_step    = int(lead.get("followup_step") or 0)
    last_email_sent = lead.get("last_email_sent")

    if status in {"replied", "completed", "opt-out", "unsubscribed"}:
        print(f"🛑 Lead stopped → {lead_email} | status={status}")
        return -1

    if status in {"new", "pending", "not_contacted", ""} or not last_email_sent:
        return 0

    if current_step >= MAX_STEP:
        print(f"🛑 Max follow-up step reached → stopping: {lead_email}")
        return -1

    # FIX: use followup_open_count (not open_count) to decide step advancement
    followup_opens = int(lead.get("followup_open_count") or 0)
    reply_count    = int(lead.get("reply_count") or 0)

    if reply_count >= 1:
        return -1

    if followup_opens == 0:
        next_step = current_step + 2
        print(f"⚠ No followup open → skipping a step for {lead_email}")
    else:
        next_step = current_step + 1
        print(f"✅ Followup opened → normal advance for {lead_email}")

    if next_step > MAX_STEP:
        next_step = MAX_STEP

    return next_step


def generate_next_email(
    lead_email:    str,
    campaign_id:   int,
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

    print(
        f"🧩 Email step debug → lead={lead_email} | step={step} "
        f"| template={template_name} | followup_type={followup_type}"
    )

    if followup_type == "soft_open":
        subject_prefix = "Quick follow-up 🔥"
    elif followup_type == "no_open":
        subject_prefix = "Checking in ❄️"
    else:
        subject_prefix = "Following up"

    email = personalize_email(lead, step=step)
    if not email:
        return {"subject": "", "body": ""}

    email["subject"]       = f"{subject_prefix} | {email.get('subject', '')}".strip(" |")
    email["followup_type"] = followup_type
    return email


def update_followup(
    lead_email:  str,
    campaign_id: int,
    step:        int,
    status:      str,
) -> None:
    timestamp = datetime.utcnow().isoformat()

    if step == -1:
        update_lead_status(
            email=lead_email,
            campaign_id=campaign_id,
            status="completed",
            metadata={"followup_completed": True, "updated_at": timestamp},
        )
        return

    update_lead_status(
        email=lead_email,
        campaign_id=campaign_id,
        followup_step=step,
        status=status,
        metadata={"last_email_sent_at": timestamp},
    )
