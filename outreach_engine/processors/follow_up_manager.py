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

FOLLOWUP_DELAYS = {0: 0, 1: 2, 2: 3, 3: 4, 4: 5}


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
    Checks BOTH counters:
      open_count          = cold email opens
      followup_open_count = followup email opens

    Any open (cold or followup) → soft_open
    No opens at all            → no_open
    """
    open_count     = int(lead.get("open_count") or 0)
    followup_opens = int(lead.get("followup_open_count") or 0)
    reply_count    = int(lead.get("reply_count") or 0)

    if reply_count >= 1:
        return "replied"
    if open_count >= 1 or followup_opens >= 1:
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

    NEW FLOW

    cold
      ↓
    followup_soft_open
      ↓
    if clicked Loom:
        followup_loom_clicked
      ↓
    if no reply:
        failed

    no_open
      ↓
    followup_no_open
      ↓
    failed
    """

    status          = (lead.get("status") or "").lower().strip()
    reply_count     = int(lead.get("reply_count") or 0)
    current_step    = int(lead.get("followup_step") or 0)
    followup_status = (lead.get("followup_status") or "").lower().strip()
    link_clicked    = bool(lead.get("link_clicked"))

    # terminal states
    if status in {
        "replied",
        "converted",
        "opt-out",
        "failed",
        "completed",
    }:
        return None

    if reply_count >= 1:
        return "__mark_replied__"

    if status != "sent":
        return None

    if current_step >= MAX_STEP:
        return "__mark_failed__"

    followup_type = _followup_email_type(lead)

    if followup_type == "replied":
        return "__mark_replied__"

    # -----------------------------------
    # SOFT OPEN FLOW
    # -----------------------------------

    if followup_type == "soft_open":

        # already sent Loom email -> stop
        if followup_status == "loom_clicked":
            return "__mark_failed__"

        # clicked Loom after soft open
        if (
            followup_status == "soft_open"
            and link_clicked
        ):
            return "followup_loom_clicked"

        # soft_open already sent and NO click
        if followup_status == "soft_open":
            return "__mark_failed__"

        return "followup_soft_open"

    # -----------------------------------
    # NO OPEN FLOW
    # -----------------------------------

    if followup_status == "no_open":
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

    reply_count = int(lead.get("reply_count") or 0)

    if reply_count >= 1:
        return -1

    # Always advance by 1 step
    next_step = current_step + 1
    print(f"✅ Step advance → {lead_email} step {current_step} → {next_step}")

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
