# outreach_engine/processors/lead_fetcher.py

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from outreach_engine.database.supabase_client import supabase

TEST_EMAIL = os.getenv("TEST_EMAIL", "").strip().lower()

# These statuses mean the lead is completely done — never touch again
TERMINAL_STATUSES = {
    "failed", "replied", "completed",
    "converted", "won", "lost", "closed",
}

# These followup_statuses mean the sequence is done
TERMINAL_FOLLOWUP_STATUSES = {
    "failed", "completed",
}


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def _normalize_text(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _status_clean(lead: Dict[str, Any]) -> str:
    return _normalize_text(lead.get("status"))


def _followup_status_clean(lead: Dict[str, Any]) -> str:
    return _normalize_text(lead.get("followup_status") or "")


# ---------------------------------------------------------------------------
# Terminal check
# ---------------------------------------------------------------------------

def _is_terminal(lead: Dict[str, Any]) -> bool:
    status          = _status_clean(lead)
    followup_status = _followup_status_clean(lead)
    reply_status    = lead.get("reply_status")

    if isinstance(reply_status, bool):
        replied = reply_status
    elif isinstance(reply_status, str):
        replied = reply_status.strip().lower() in {"1", "true", "yes", "replied"}
    else:
        replied = False

    return (
        status in TERMINAL_STATUSES
        or followup_status in TERMINAL_FOLLOWUP_STATUSES
        or replied
    )


# ---------------------------------------------------------------------------
# 24h gap check
# ---------------------------------------------------------------------------

def _next_followup_passed(lead: Dict[str, Any]) -> bool:
    """
    Returns True if the lead is due for a follow-up.

    FIX: NULL next_followup no longer means "due immediately".
    A lead that was just sent a cold email will have next_followup=NULL
    if it was inserted before the _mark_sent fix. Treating NULL as
    "always due" caused the followup engine to fire in the same cycle
    as the cold send for those leads.

    New rules:
    - next_followup is NULL AND last_email_sent is NULL → cold lead, not a followup candidate
    - next_followup is NULL AND last_email_sent is set  → legacy sent lead, treat as due
      (best-effort: we don't want to block old leads forever)
    - next_followup is in the future → not due (False)
    - next_followup is in the past   → due (True)
    """
    next_followup   = lead.get("next_followup")
    last_email_sent = lead.get("last_email_sent")

    if not next_followup:
        # NULL next_followup + NULL last_email_sent = never emailed = cold lead
        # The followup engine should not touch these.
        if not last_email_sent:
            return False
        # NULL next_followup + has last_email_sent = legacy sent lead
        # Treat as due so old leads aren't silently dropped.
        return True

    try:
        if isinstance(next_followup, str):
            nxt = datetime.fromisoformat(
                next_followup.replace("Z", "+00:00")
            )
        else:
            nxt = next_followup

        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)

        now    = datetime.now(timezone.utc)
        is_due = now >= nxt

        if not is_due:
            remaining = (nxt - now).total_seconds() / 3600
            print(
                f"  ⏳ Not due yet | "
                f"next_followup={next_followup} | "
                f"remaining={remaining:.1f}h"
            )

        return is_due

    except Exception as e:
        print(f"  ⚠ next_followup parse error: {e} | value={next_followup}")
        return True


# ---------------------------------------------------------------------------
# Cold outreach eligibility
# ---------------------------------------------------------------------------

def _is_initial_eligible(lead: Dict[str, Any]) -> bool:
    status          = _status_clean(lead)
    last_email_sent = lead.get("last_email_sent")
    followup_step   = _to_int(lead.get("followup_step"))

    return (
        status in {"new", "pending", "not_contacted", ""}
        and last_email_sent is None
        and followup_step == 0
        and not _is_terminal(lead)
    )


# ---------------------------------------------------------------------------
# Follow-up eligibility
# ---------------------------------------------------------------------------

def _is_followup_eligible(lead: Dict[str, Any]) -> bool:
    """
    Eligible for follow-up when ALL of these are true:
    - status = 'sent'
    - last_email_sent is NOT NULL
    - followup_status NOT in terminal states
    - next_followup has passed
    """
    status          = _status_clean(lead)
    followup_status = _followup_status_clean(lead)

    if status != "sent":
        return False

    if lead.get("last_email_sent") is None:
        return False

    if followup_status in TERMINAL_FOLLOWUP_STATUSES:
        return False

    if not _next_followup_passed(lead):
        return False

    return True


# ---------------------------------------------------------------------------
# Follow-up type computation
# ---------------------------------------------------------------------------

def _compute_followup_type(lead: Dict[str, Any]) -> Optional[str]:
    """
    Decide which follow-up email to send based on lead state.

    FIX (Bug 1 + Bug 3): Use followup_open_count — NOT open_count —
    to determine whether the followup email was opened.

    open_count         = cold email opens  (pixel built with email_type=cold)
    followup_open_count = followup email opens (pixel built with email_type=followup)

    The old code checked open_count here, which meant:
      - A cold email open (open_count=1) would immediately route the NEXT
        followup to 'followup_soft_open' even before any followup was sent.
      - A followup email open (followup_open_count=1, open_count still 0)
        was invisible, so the system kept sending 'followup_no_open' instead
        of upgrading to 'followup_soft_open'.

    Correct routing:
      followup_status=NULL (cold email sent, no followup yet):
        reply > 0            → None  (state machine marks replied)
        followup_open = 0    → 'followup_no_open'
        followup_open > 0    → 'followup_soft_open'  ← open_count is IRRELEVANT here

      followup_status='no_open' (followup_no_open was sent):
        reply > 0            → None  (state machine marks replied)
        followup_open = 0    → None  (state machine marks failed)
        followup_open > 0    → 'followup_soft_open'

      followup_status='soft_open' (followup_soft_open was sent):
        → None always (state machine marks failed or replied)
    """
    if not _is_followup_eligible(lead):
        return None

    # FIX: use followup_open_count, not open_count
    followup_open_count = _to_int(lead.get("followup_open_count"))
    reply_count         = _to_int(lead.get("reply_count"))
    followup_status     = _followup_status_clean(lead)

    # ── followup_status = NULL (just received cold email) ────────────────────
    if not followup_status:
        if reply_count > 0:
            return None
        if followup_open_count == 0:
            return "followup_no_open"
        return "followup_soft_open"

    # ── followup_status = 'no_open' (followup_no_open was sent) ──────────────
    elif followup_status == "no_open":
        if reply_count > 0:
            return None
        if followup_open_count == 0:
            return None          # → state machine marks failed
        return "followup_soft_open"

    # ── followup_status = 'soft_open' (followup_soft_open was sent) ──────────
    elif followup_status == "soft_open":
        return None              # → state machine marks failed or replied

    return None


# ---------------------------------------------------------------------------
# Lead normalizer
# ---------------------------------------------------------------------------

def normalize_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
    lead_id    = lead.get("id") or lead.get("lead_id") or lead.get("uuid")
    first_name = lead.get("first_name")
    last_name  = lead.get("last_name")
    name       = " ".join(filter(None, [first_name, last_name])) or None
    metadata   = lead.get("metadata") or {}

    normalized = {
        "id":                    lead_id,
        "name":                  name,
        "first_name":            first_name,
        "last_name":             last_name,
        "email":                 lead.get("email"),
        "company":               lead.get("company"),
        "industry":              lead.get("industry"),
        "lead_source":           lead.get("lead_source"),
        "campaign_id":           lead.get("campaign_id"),
        "country":               lead.get("country"),
        "tech_stack":            (
            lead.get("tech_stack") or metadata.get("tech_stack")
        ),
        "pain_points":           (
            lead.get("pain_points") or metadata.get("pain_points")
        ),
        "automation_maturity":   (
            lead.get("automation_maturity")
            or metadata.get("automation_maturity")
        ),
        "status":                lead.get("status"),
        "followup_status":       lead.get("followup_status"),
        "reply_status":          lead.get("reply_status"),
        "replied_at":            lead.get("replied_at"),
        "open_count":            _to_int(lead.get("open_count", 0)),
        "click_count":           _to_int(lead.get("click_count", 0)),
        "reply_count":           _to_int(lead.get("reply_count", 0)),
        "conversion_count":      _to_int(lead.get("conversion_count", 0)),
        "followup_open_count":   _to_int(lead.get("followup_open_count", 0)),
        "last_email_sent":       lead.get("last_email_sent"),
        "last_followup_sent_at": lead.get("last_followup_sent_at"),
        "next_followup":         lead.get("next_followup"),
        "thread_id":             lead.get("thread_id"),
        "gmail_message_id":      lead.get("gmail_message_id"),
        "email_opened":          lead.get("email_opened"),
        "followup_step":         _to_int(lead.get("followup_step", 0)),
        "score":                 lead.get("score"),
        "raw":                   lead,
    }

    normalized["followup_type"] = _compute_followup_type(normalized)
    return normalized


# ---------------------------------------------------------------------------
# Filter ready leads
# ---------------------------------------------------------------------------

def _filter_ready_leads(
    normalized: List[Dict[str, Any]], mode: str
) -> List[Dict[str, Any]]:
    mode = _normalize_text(mode) or "cold"

    if mode == "cold":
        return [
            lead for lead in normalized
            if lead.get("email")
            and lead.get("id")
            and _is_initial_eligible(lead)
        ]

    if mode == "followups":
        return [
            lead for lead in normalized
            if lead.get("email")
            and lead.get("id")
            and _is_followup_eligible(lead)
        ]

    return [
        lead for lead in normalized
        if lead.get("email")
        and lead.get("id")
        and (
            _is_initial_eligible(lead)
            or _is_followup_eligible(lead)
        )
    ]


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def get_ready_leads(
    min_score: float = 0,
    country: Optional[str] = None,
    tech_stack: Optional[str] = None,
    pain_point: Optional[str] = None,
    automation_maturity: Optional[str] = None,
    campaign_id: Optional[int] = None,
    mode: str = "cold",
) -> List[Dict[str, Any]]:
    """
    Fetch leads ready for outreach.

    mode:
      "cold"      → status in (new, pending, not_contacted), never emailed
      "followups" → status='sent', next_followup passed
      "all"       → both cold and follow-up leads
    """
    mode = _normalize_text(mode) or "cold"
    print(f"\n🚨 FETCHING LEADS (MODE: {mode.upper()})\n")

    query = supabase.table("outreach_leads").select("*")

    if campaign_id is not None:
        query = query.eq("campaign_id", campaign_id)

    if mode == "cold":
        query = query.in_(
            "status", ["new", "pending", "not_contacted"]
        )
    elif mode == "followups":
        query = query.eq("status", "sent")

    response = query.execute()
    leads    = response.data or []

    print(f"📦 Raw leads from DB: {len(leads)}")

    if not leads:
        print(f"  ⚠ No leads returned from DB for mode='{mode}'")
        if mode == "followups":
            print(
                "  ℹ Check: are there leads with status='sent' "
                "in outreach_leads?"
            )
        elif mode == "cold":
            print(
                "  ℹ Check: are there leads with status in "
                "('new','pending','not_contacted') in outreach_leads?"
            )

    normalized = [normalize_lead(lead) for lead in leads]

    for lead in normalized[:10]:
        print(
            f"  DEBUG → id:{lead['id']} | "
            f"{lead['email']} | "
            f"status:{lead['status']} | "
            f"followup_status:{lead['followup_status']} | "
            f"open:{lead['open_count']} | "
            f"followup_open:{lead['followup_open_count']} | "
            f"reply:{lead['reply_count']} | "
            f"followup_type:{lead['followup_type']} | "
            f"next_followup:{lead['next_followup']} | "
            f"due:{_next_followup_passed(lead)}"
        )

    ready = _filter_ready_leads(normalized, mode)

    if TEST_EMAIL:
        emails_in_ready = {_normalize_text(l.get("email")) for l in ready}
        if TEST_EMAIL in emails_in_ready:
            ready = [
                l for l in ready
                if _normalize_text(l.get("email")) == TEST_EMAIL
            ]
            print(f"\n🧪 TEST MODE → filtering to: {TEST_EMAIL}\n")
        else:
            print(
                f"\n⚠ TEST_EMAIL='{TEST_EMAIL}' not found in ready leads\n"
            )
    else:
        print("\n🚀 NORMAL MODE\n")

    if country:
        ready = [l for l in ready if l.get("country") == country]
    if tech_stack:
        ready = [
            l for l in ready
            if l.get("tech_stack")
            and tech_stack.lower() in str(l["tech_stack"]).lower()
        ]
    if pain_point:
        ready = [
            l for l in ready
            if l.get("pain_points")
            and pain_point.lower() in str(l["pain_points"]).lower()
        ]
    if automation_maturity:
        ready = [
            l for l in ready
            if l.get("automation_maturity") == automation_maturity
        ]
    if min_score > 0:
        ready = [
            l for l in ready
            if _to_float(l.get("score"), 0.0) >= float(min_score)
        ]

    print(f"✅ READY COUNT: {len(ready)}\n")
    return ready


async def async_get_ready_leads(
    min_score: float = 0,
    country: Optional[str] = None,
    tech_stack: Optional[str] = None,
    pain_point: Optional[str] = None,
    automation_maturity: Optional[str] = None,
    campaign_id: Optional[int] = None,
    mode: str = "cold",
) -> List[Dict[str, Any]]:
    """Async wrapper around get_ready_leads."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: get_ready_leads(
            min_score=min_score,
            country=country,
            tech_stack=tech_stack,
            pain_point=pain_point,
            automation_maturity=automation_maturity,
            campaign_id=campaign_id,
            mode=mode,
        ),
    )
