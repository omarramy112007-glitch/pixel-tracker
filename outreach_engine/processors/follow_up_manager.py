# outreach_engine/processors/follow_up_manager.py


from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from outreach_engine.core.email_sequences import get_email_for_step
from outreach_engine.core.lead_manager import get_lead
from outreach_engine.database.supabase_client import supabase
from outreach_engine.processors.email_personalizer import personalize_email


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_STEP = 4

# Statuses that mean we should never send anything
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

# Statuses that mean the lead hasn't been contacted yet
INITIAL_STATUSES = {"new", "pending", "not_contacted", ""}

# Statuses that mean we are in an active follow-up sequence
ACTIVE_STATUSES = {"sent", "processing", "rate_limited", "contacted"}

# Sequence names
SEQUENCE_A = "cold_sequence"        # No reply, not opened
SEQUENCE_B = "warm_sequence"        # Opened but no reply
SEQUENCE_C = "interested_sequence"  # Replied / interested — used for manual guidance only

# Follow-up delays per step (hours)
FOLLOWUP_DELAYS_HOURS: Dict[int, int] = {
    0: 48,   # First follow-up: 2 days after initial send
    1: 72,   # Second: 3 days after first follow-up
    2: 96,   # Third: 4 days
    3: 120,  # Fourth: 5 days
}

# Subject prefixes per step
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


# ---------------------------------------------------------------------------
# Sequence selection
# ---------------------------------------------------------------------------

def _select_sequence(lead: Dict) -> str:
    """
    Choose which sequence to use based on lead state.
    Clicks are explicitly excluded from this decision.

    Rules:
      - opened (open_count > 0) AND no reply → Sequence B (warm)
      - otherwise → Sequence A (cold)
      - replied / interested → Sequence C (not auto-sent)
    """
    status = _normalize(lead.get("status"))

    if status in STOP_STATUSES:
        return SEQUENCE_C  # Will be blocked by STOP check anyway

    open_count = int(lead.get("open_count") or 0)

    if open_count > 0:
        return SEQUENCE_B  # Lead opened at least one email — use warmer tone

    return SEQUENCE_A  # Cold — never opened


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

    # --- Hard stop ---
    if status in STOP_STATUSES:
        print(f"🛑 Follow-up stopped → {lead_email} | status={status}")
        return -1

    # --- Max step reached ---
    if current_step >= MAX_STEP:
        print(f"🛑 Max step reached → {lead_email}")
        return -1

    # --- Not yet contacted → start from step 0 ---
    if status in INITIAL_STATUSES:
        return 0

    # --- Active: check if the delay window has passed ---
    if status in ACTIVE_STATUSES:
        next_followup_dt = _parse_dt(lead.get("next_followup"))
        if next_followup_dt and _now_utc() < next_followup_dt:
            # Still inside the wait window — do not send yet
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

    Sequence is auto-selected based on lead state unless overridden.
    Returns {"subject": str, "body": str} or empty dict if nothing to send.
    """
    step = determine_next_step(lead_email, campaign_id)

    if step == -1:
        return {"subject": "", "body": ""}

    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return {"subject": "", "body": ""}

    # Auto-select sequence based on state
    chosen_sequence = sequence_name or _select_sequence(lead)

    template_name = get_email_for_step(chosen_sequence, step) or "cold_email"
    print(f"🧩 Email step → lead={lead_email} | step={step} | sequence={chosen_sequence} | template={template_name}")

    email = personalize_email(lead, step=step)
    if not email:
        return {"subject": "", "body": ""}

    subject_prefix = SUBJECT_PREFIXES.get(step, "Following up")
    base_subject = email.get("subject") or ""
    email["subject"] = f"{subject_prefix} | {base_subject}".strip(" |")

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
    """
    timestamp = _now_iso()

    if step == -1:
        # Sequence exhausted
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

    payload = {
        "followup_step": step,
        "status": "completed" if step >= MAX_STEP else status,
        "last_email_sent": timestamp,
        "next_followup": next_followup.isoformat() if next_followup else None,
        "last_updated": timestamp,
    }

    _update_outreach_lead(lead_email, campaign_id, payload)


# ---------------------------------------------------------------------------
# Open signal handler (called by event_router)
# ---------------------------------------------------------------------------

def on_open_signal(lead_id: int, campaign_id: int) -> Dict[str, str]:
    """
    Called by event_router when an 'opened' event is received.

    Rule: open = signal only.
    We do NOT trigger an immediate follow-up here.
    We simply ensure the lead is on Sequence B (warm) for the next scheduled send.

    The scheduler will use determine_next_step() and _select_sequence()
    to pick the right email when the time window comes.
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

        # Don't interfere with terminal or active-reply states
        if status in STOP_STATUSES:
            return {"action": "skipped", "reason": "stop_status"}

        open_count = int(row.get("open_count") or 0)

        # Just log that we received the signal — sequence selection happens
        # at send time in generate_next_email() / _select_sequence()
        print(f"📬 Open signal received → lead_id={lead_id} | open_count={open_count + 1} | sequence=warm")

        return {"action": "signal_received", "sequence": "warm", "open_count": open_count + 1}

    except Exception as e:
        print(f"⚠️ on_open_signal failed: {e}")
        return {"action": "error", "error": str(e)}
