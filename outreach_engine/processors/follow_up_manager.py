# outreach_engine/processors/follow_up_manager.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.core.email_sequences import get_email_for_step
from outreach_engine.core.lead_manager import get_lead, update_lead_status
from outreach_engine.analytics.lead_scoring import calculate_engagement_score

MAX_STEP = 4
LOW_ENGAGEMENT_THRESHOLD = 1
HIGH_ENGAGEMENT_THRESHOLD = 4


def determine_next_step(lead_email: str, campaign_id: int) -> int:
    """
    Determine the next follow-up step based on lead status + engagement.
    Returns:
        -1  => stop sending
         0+ => next email step
    """
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return 0

    status = (lead.get("status") or "").lower().strip()
    current_step = int(lead.get("followup_step") or 0)

    if status in {"replied", "completed", "opt-out", "unsubscribed", "converted", "cancelled", "failed"}:
        print(f"🛑 Lead stopped → {lead_email} | status={status}")
        return -1

    if current_step >= MAX_STEP:
        print(f"🛑 Max follow-up step reached → stopping: {lead_email}")
        return -1

    # New lead -> send step 0
    if status in {"new", "pending", "not_contacted", ""}:
        return 0

    # Anything already sent should move forward, even if last_email_sent was missing
    score = calculate_engagement_score(lead)

    if score <= LOW_ENGAGEMENT_THRESHOLD:
        next_step = current_step + 2
        print(f"⚠ Low engagement → skipping a step for {lead_email}")
    else:
        next_step = current_step + 1
        if score >= HIGH_ENGAGEMENT_THRESHOLD:
            print(f"🔥 High engagement → advancing faster for {lead_email}")

    if next_step > MAX_STEP:
        next_step = MAX_STEP

    return next_step


def generate_next_email(
    lead_email: str,
    campaign_id: int,
    sequence_name: str = "automation_outreach",
) -> Dict[str, str]:
    """
    Generate the next email body + subject for a lead.
    """
    step = determine_next_step(lead_email, campaign_id)

    if step == -1:
        return {"subject": "", "body": ""}

    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return {"subject": "", "body": ""}

    template_name = get_email_for_step(sequence_name, step) or "cold_email"
    print(f"🧩 Email step debug → lead={lead_email} | step={step} | template={template_name}")

    score = calculate_engagement_score(lead)

    if score >= HIGH_ENGAGEMENT_THRESHOLD:
        subject_prefix = "Quick follow-up 🔥"
    elif score <= LOW_ENGAGEMENT_THRESHOLD:
        subject_prefix = "Checking in ❄️"
    else:
        subject_prefix = "Following up"

    email = personalize_email(lead, step=step)
    if not email:
        return {"subject": "", "body": ""}

    subject = email.get("subject") or ""
    body = email.get("body") or ""

    email["subject"] = f"{subject_prefix} | {subject}".strip(" |")
    email["body"] = body

    return email


def update_followup(
    lead_email: str,
    campaign_id: int,
    step: int,
    status: str,
) -> None:
    """
    Update lead status + follow-up progress after send / completion.
    """
    timestamp = datetime.now(timezone.utc).isoformat()

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