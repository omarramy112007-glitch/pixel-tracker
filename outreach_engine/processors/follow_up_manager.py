# outreach_engine/processors/follow_up_manager.py

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from outreach_engine.core.templates import TEMPLATES
from outreach_engine.core.lead_manager import get_lead, update_lead_status
from outreach_engine.database.supabase_client import supabase
from outreach_engine.processors.email_personalizer import personalize_email


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_STEP = 4

STOP_STATUSES = {
    "replied",
    "interested",
    "completed",
    "converted",
    "opt-out",
    "unsubscribed",
    "cancelled",
    "failed",
}

INITIAL_STATUSES = {"new", "pending", "not_contacted", ""}
ACTIVE_STATUSES = {"sent", "processing", "rate_limited", "contacted"}

# Follow-up delays per step (hours)
FOLLOWUP_DELAYS_HOURS: Dict[int, int] = {
    0: 48,   # first follow-up after initial send
    1: 72,
    2: 96,
    3: 120,
}

SUBJECT_PREFIXES: Dict[int, str] = {
    0: "Quick question",
    1: "Following up",
    2: "Checking in",
    3: "Last note",
    4: "Final follow-up",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _normalize(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _parse_dt(value) -> Optional[datetime]:
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


def _next_followup_dt(step: int) -> Optional[datetime]:
    hours = FOLLOWUP_DELAYS_HOURS.get(step)
    if hours is None:
        return None
    return _now_utc() + timedelta(hours=hours)


def _update_outreach_lead(email: str, campaign_id: int, payload: Dict) -> None:
    try:
        supabase.table("outreach_leads") \
            .update(payload) \
            .eq("email", email) \
            .eq("campaign_id", campaign_id) \
            .execute()
    except Exception as e:
        print(f"⚠️ update_outreach_lead failed → {email}: {e}")


def _enqueue_next_followup(lead_id: int, next_step: int, delay_hours: int, reason: str = "scheduled_followup") -> None:
    try:
        from outreach_engine.core.queue import enqueue_followup
        enqueue_followup(
            lead_id=lead_id,
            followup_step=next_step,
            delay_hours=delay_hours,
            reason=reason,
        )
    except Exception as e:
        print(f"⚠️ enqueue_next_followup failed → lead_id={lead_id}: {e}")


# ---------------------------------------------------------------------------
# Sequence selection / template selection
# ---------------------------------------------------------------------------

def _select_template_name(lead: Dict, step: int) -> str:
    """
    Template selection for render/preview.
    Clicks are NOT used here.
    """
    status = _normalize(lead.get("status"))
    open_count = int(lead.get("open_count") or 0)

    if status == "interested":
        return "interested_followup"

    if step == 0:
        return "initial_outreach"

    if open_count > 0:
        return "followup_soft_open"

    return "followup_no_open"


# ---------------------------------------------------------------------------
# Core decision: what's the next step?
# ---------------------------------------------------------------------------

def determine_next_step(lead_email: str, campaign_id: int) -> int:
    """
    Decide the next follow-up step index for a lead.

    Returns:
      int >= 0  → the next step to send
      -1        → do NOT send (stopped or not ready)

    Decision is based on lead STATUS only.
    Clicks are NOT part of this logic.
    """
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return -1

    status = _normalize(lead.get("status"))
    current_step = int(lead.get("followup_step") or 0)

    if status in STOP_STATUSES:
        print(f"🛑 Follow-up stopped → {lead_email} | status={status}")
        return -1

    if current_step >= MAX_STEP:
        print(f"🛑 Max step reached → {lead_email}")
        return -1

    if status in INITIAL_STATUSES:
        return 0

    if status in ACTIVE_STATUSES:
        next_followup_dt = _parse_dt(lead.get("next_followup"))
        if next_followup_dt and _now_utc() < next_followup_dt:
            return -1
        return min(current_step + 1, MAX_STEP)

    return -1


# ---------------------------------------------------------------------------
# Email generation
# ---------------------------------------------------------------------------

def generate_next_email(
    lead_email: str,
    campaign_id: int,
    sequence_name: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generate the next email body + subject for a lead.

    Returns {"subject": str, "body": str} or empty dict if nothing to send.
    """
    step = determine_next_step(lead_email, campaign_id)

    if step == -1:
        return {"subject": "", "body": ""}

    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return {"subject": "", "body": ""}

    template_name = sequence_name or _select_template_name(lead, step)
    template = TEMPLATES.get(template_name) or TEMPLATES.get("initial_outreach") or {}

    email = personalize_email(lead, step=step)
    if not email:
        return {"subject": "", "body": ""}

    # Prefer the template body/subject when available, otherwise fall back to the
    # existing personalizer output.
    subject_prefix = SUBJECT_PREFIXES.get(step, "Following up")
    base_subject = template.get("subject") or email.get("subject") or ""
    base_body = template.get("body") or email.get("body") or ""

    email["subject"] = f"{subject_prefix} | {base_subject}".strip(" |")
    email["body"] = base_body

    return email


# ---------------------------------------------------------------------------
# State update after send
# ---------------------------------------------------------------------------

def update_followup(
    lead_email: str,
    campaign_id: int,
    step: int,
    status: str,
) -> None:
    """
    Update lead follow-up state after an email is sent or sequence completes.

    This also enqueues the next follow-up, so the queue worker can handle the
    next send later without direct scheduler sending.
    """
    timestamp = _now_iso()

    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return

    lead_id = lead.get("id")
    if step == -1:
        _update_outreach_lead(
            lead_email,
            campaign_id,
            {
                "status": "completed",
                "followup_step": MAX_STEP,
                "next_followup": None,
                "last_updated": timestamp,
            },
        )
        return

    next_followup = _next_followup_dt(step)
    next_step = min(step + 1, MAX_STEP)

    payload = {
        "followup_step": step,
        "status": "completed" if step >= MAX_STEP else status,
        "last_email_sent": timestamp,
        "next_followup": next_followup.isoformat() if next_followup else None,
        "last_updated": timestamp,
    }

    _update_outreach_lead(lead_email, campaign_id, payload)

    # Queue the next follow-up only if the lead is still active and we are not
    # at the final step.
    if lead_id and next_step <= MAX_STEP and status not in STOP_STATUSES:
        delay_hours = FOLLOWUP_DELAYS_HOURS.get(step, 72)
        _enqueue_next_followup(
            lead_id=int(lead_id),
            next_step=next_step,
            delay_hours=delay_hours,
            reason="auto_followup_after_send",
        )


# ---------------------------------------------------------------------------
# Open signal handler (called by event_router)
# ---------------------------------------------------------------------------

def on_open_signal(lead_id: int, campaign_id: int) -> Dict[str, str]:
    """
    Called by event_router when an 'opened' event is received.

    Rule: open = signal only.
    We do NOT trigger an immediate follow-up here.
    """
    try:
        existing = (
            supabase.table("outreach_leads")
            .select("status, followup_step, open_count")
            .eq("id", lead_id)
            .eq("campaign_id", campaign_id)
            .limit(1)
            .execute()
        )

        if not existing.data:
            return {"action": "no_lead_found"}

        row = existing.data[0]
        status = _normalize(row.get("status"))

        if status in STOP_STATUSES:
            return {"action": "skipped", "reason": "stop_status"}

        open_count = int(row.get("open_count") or 0)
        print(f"📬 Open signal received → lead_id={lead_id} | open_count={open_count + 1} | sequence=warm")

        return {"action": "signal_received", "sequence": "warm", "open_count": open_count + 1}

    except Exception as e:
        print(f"⚠️ on_open_signal failed: {e}")
        return {"action": "error", "error": str(e)}
