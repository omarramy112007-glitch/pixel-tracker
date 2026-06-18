# outreach_engine/processors/lead_fetcher.py

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from outreach_engine.database.supabase_client import supabase

TEST_EMAIL = os.getenv("TEST_EMAIL", "").strip().lower()

TERMINAL_STATUSES = {
    "failed", "replied", "completed",
    "converted", "won", "lost", "closed",
}

TERMINAL_FOLLOWUP_STATUSES = {
    "failed", "completed",
}


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


def _next_followup_passed(lead: Dict[str, Any]) -> bool:
    next_followup   = lead.get("next_followup")
    last_email_sent = lead.get("last_email_sent")

    if not next_followup:
        if not last_email_sent:
            return False
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


def _is_followup_eligible(lead: Dict[str, Any]) -> bool:
    """
    FIX: removed the followup_status == 'soft_open' early exit.
    Previously this returned False for soft_open leads, which filtered
    them out before decide_followup_action() could check link_clicked.
    Now soft_open leads with a passed next_followup are eligible —
    decide_followup_action() handles what to do with them.
    """
    status          = _status_clean(lead)
    followup_status = _followup_status_clean(lead)

    if status != "sent":
        return False

    if lead.get("last_email_sent") is None:
        return False

    if followup_status in TERMINAL_FOLLOWUP_STATUSES:
        return False

    # loom_clicked email already sent and no reply → terminal
    if followup_status == "loom_clicked":
        return False

    if not _next_followup_passed(lead):
        return False

    return True


def _compute_followup_type(lead: Dict[str, Any]) -> Optional[str]:
    """
    FIX: added loom_clicked path and stopped returning None for
    soft_open leads that have link_clicked=True. Previously returning
    None here caused the lead to be silently dropped by _filter_ready_leads.
    """
    if not _is_followup_eligible(lead):
        return None

    followup_open_count = _to_int(lead.get("followup_open_count"))
    reply_count         = _to_int(lead.get("reply_count"))
    followup_status     = _followup_status_clean(lead)
    link_clicked        = bool(lead.get("link_clicked"))
    reply_status        = lead.get("reply_status")

    if isinstance(reply_status, bool):
        replied = reply_status
    elif isinstance(reply_status, str):
        replied = reply_status.strip().lower() in {"1", "true", "yes", "replied"}
    else:
        replied = False

    # reply detected → state machine handles it
    if replied or reply_count > 0:
        return "replied"

    # ── followup_status = NULL (cold email sent, no followup yet) ────────────
    if not followup_status:
        if followup_open_count == 0:
            return "followup_no_open"
        return "followup_soft_open"

    # ── followup_status = 'no_open' ──────────────────────────────────────────
    elif followup_status == "no_open":
        if followup_open_count == 0:
            return None  # state machine marks failed
        return "followup_soft_open"

    # ── followup_status = 'soft_open' ────────────────────────────────────────
    elif followup_status == "soft_open":
        # FIX: if Loom was clicked → route to loom_clicked followup
        if link_clicked:
            return "followup_loom_clicked"
        # no click, no reply → state machine marks failed
        return None

    return None


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
        "link_clicked":          bool(lead.get("link_clicked")),
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


def get_ready_leads(
    min_score: float = 0,
    country: Optional[str] = None,
    tech_stack: Optional[str] = None,
    pain_point: Optional[str] = None,
    automation_maturity: Optional[str] = None,
    campaign_id: Optional[int] = None,
    mode: str = "cold",
) -> List[Dict[str, Any]]:
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
            f"link_clicked:{lead['link_clicked']} | "
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
