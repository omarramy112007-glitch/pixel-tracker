from __future__ import annotations

from typing import Dict
from datetime import datetime

from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.core.email_sequences import get_email_for_step
from outreach_engine.core.lead_manager import get_lead, update_lead_status
from outreach_engine.analytics.lead_scoring import calculate_engagement_score

MAX_STEP                  = 4
LOW_ENGAGEMENT_THRESHOLD  = 1
HIGH_ENGAGEMENT_THRESHOLD = 4


def _followup_email_type(lead: Dict) -> str:
    """
    Decide which follow-up template category to use based on
    followup_open_count (opens of the followup email itself),
    NOT the cold email's open_count.

    - followup_open_count == 0  → lead never opened the follow-up → no_open path
    - followup_open_count >= 1  → lead opened but didn't reply    → soft_open path
    - reply_count >= 1          → should have exited pipeline already, but guard anyway
    """
    followup_opens = int(lead.get("followup_open_count") or 0)
    reply_count    = int(lead.get("reply_count") or 0)

    if reply_count >= 1:
        return "replied"
    if followup_opens >= 1:
        return "soft_open"
    return "no_open"


def determine_next_step(lead_email: str, campaign_id: int) -> int:
    """
    Determine the next follow-up step based on lead status + engagement.
    Uses followup_open_count (not open_count) for post-send decisions.
    """
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return 0

    status        = (lead.get("status") or "").lower().strip()
    current_step  = int(lead.get("followup_step") or 0)
    last_email_sent = lead.get("last_email_sent")

    if status in {"replied", "completed", "opt-out", "unsubscribed"}:
        print(f"🛑 Lead stopped → {lead_email} | status={status}")
        return -1

    if status in {"new", "pending", "not_contacted", ""} or not last_email_sent:
        return 0

    if current_step >= MAX_STEP:
        print(f"🛑 Max follow-up step reached → stopping: {lead_email}")
        return -1

    # Use followup_open_count to judge engagement with the followup email,
    # NOT open_count (which only reflects the cold email).
    followup_opens = int(lead.get("followup_open_count") or 0)
    reply_count    = int(lead.get("reply_count") or 0)

    if reply_count >= 1:
        return -1

    # Low engagement = followup was never opened → skip a step (aggressive)
    # High engagement = followup was opened → normal advance
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
    """
    Generate the next email body + subject for a lead.
    Subject prefix is now driven by followup_open_count, not generic engagement score.
    """
    step = determine_next_step(lead_email, campaign_id)
    if step == -1:
        return {"subject": "", "body": ""}

    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return {"subject": "", "body": ""}

    template_name  = get_email_for_step(sequence_name, step) or "cold_email"
    followup_type  = _followup_email_type(lead)

    print(
        f"🧩 Email step debug → lead={lead_email} | step={step} "
        f"| template={template_name} | followup_type={followup_type}"
    )

    # Subject prefix driven by whether the followup itself was opened
    if followup_type == "soft_open":
        subject_prefix = "Quick follow-up 🔥"   # they opened, nudge them
    elif followup_type == "no_open":
        subject_prefix = "Checking in ❄️"        # they never opened, pattern-interrupt
    else:
        subject_prefix = "Following up"

    email = personalize_email(lead, step=step)
    if not email:
        return {"subject": "", "body": ""}

    subject = email.get("subject") or ""
    body    = email.get("body") or ""

    email["subject"]      = f"{subject_prefix} | {subject}".strip(" |")
    email["body"]         = body
    email["followup_type"] = followup_type   # carry it forward for template selection

    return email


def update_followup(
    lead_email:  str,
    campaign_id: int,
    step:        int,
    status:      str,
) -> None:
    """
    Update lead status + follow-up progress after send / completion.
    """
    timestamp = datetime.utcnow().isoformat()

    if step == -1:
        update_lead_status(
            email=lead_email,
            campaign_id=campaign_id,
            status="completed",
            metadata={
                "followup_completed": True,
                "updated_at":         timestamp,
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
