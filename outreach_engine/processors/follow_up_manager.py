# File: outreach_engine/processors/follow_up_manager.py

from typing import Dict
from datetime import datetime

from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.core.email_sequences import get_email_for_step
from outreach_engine.core.lead_manager import get_lead, update_lead_status
from outreach_engine.analytics.lead_scoring import calculate_engagement_score

MAX_STEP = 4
LOW_ENGAGEMENT_THRESHOLD = 1
HIGH_ENGAGEMENT_THRESHOLD = 4


# ---------------------------------------------------
# Determine Next Step (SMART)
# ---------------------------------------------------
def determine_next_step(lead_email: str, campaign_id: int) -> int:
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return 0

    if (lead.get("status") or "").lower() == "replied":
        print(f"🛑 Lead replied → stopping: {lead_email}")
        return -1

    current_step = lead.get("followup_step", 0) or 0
    score = calculate_engagement_score(lead)

    if score <= LOW_ENGAGEMENT_THRESHOLD:
        next_step = current_step + 2
        print(f"⚠ Low engagement → skipping step for {lead_email}")

    elif score >= HIGH_ENGAGEMENT_THRESHOLD:
        next_step = current_step
        print(f"🔥 High engagement → repeating step for {lead_email}")

    else:
        next_step = current_step + 1

    if next_step > MAX_STEP:
        return -1

    return next_step


# ---------------------------------------------------
# Generate Next Email
# ---------------------------------------------------
def generate_next_email(
    lead_email: str,
    campaign_id: int,
    sequence_name: str = "automation_outreach"
) -> Dict[str, str]:
    step = determine_next_step(lead_email, campaign_id)

    if step == -1:
        return {"subject": "", "body": ""}

    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return {"subject": "", "body": ""}

    # Kept for sequence logic / future expansion
    _template_name = get_email_for_step(sequence_name, step) or "cold_email"

    score = calculate_engagement_score(lead)

    if score >= HIGH_ENGAGEMENT_THRESHOLD:
        subject_prefix = "Quick follow-up 🔥"
    elif score <= LOW_ENGAGEMENT_THRESHOLD:
        subject_prefix = "Checking in ❄️"
    else:
        subject_prefix = "Following up"

    email = personalize_email(
        lead,
        step=step
    )

    if not email:
        return {"subject": "", "body": ""}

    subject = email.get("subject") or ""
    body = email.get("body") or ""

    email["subject"] = f"{subject_prefix} | {subject}"
    email["body"] = body
    return email


# ---------------------------------------------------
# Update Lead Status (FIXED)
# ---------------------------------------------------
def update_followup(
    lead_email: str,
    campaign_id: int,
    step: int,
    status: str
) -> None:
    timestamp = datetime.utcnow().isoformat()

    if step == -1:
        update_lead_status(
            email=lead_email,
            campaign_id=campaign_id,
            status="completed",
            metadata={
                "followup_completed": True,
                "updated_at": timestamp
            }
        )
        return

    update_lead_status(
        email=lead_email,
        campaign_id=campaign_id,
        followup_step=step,
        status=status,
        metadata={
            "last_email_sent_at": timestamp
        }
    )