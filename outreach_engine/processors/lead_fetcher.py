# outreach_engine/processors/lead_fetcher.py

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from outreach_engine.database.supabase_client import supabase

TEST_EMAIL = os.getenv("TEST_EMAIL", "").strip().lower()

# Terminal statuses — never re-enter the queue
TERMINAL_STATUSES = {"failed", "replied", "completed", "converted", "won", "lost", "closed"}

# Follow-up terminal statuses — lead has been fully processed
TERMINAL_FOLLOWUP_STATUSES = {"completed", "failed"}


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
    """Lead is in a terminal state and must never be processed again."""
    status          = _status_clean(lead)
    followup_status = _followup_status_clean(lead)
    reply_count     = _to_int(lead.get("reply_count"))
    reply_status    = lead.get("reply_status")

    if isinstance(reply_status, bool):
        replied = reply_status
    elif isinstance(reply_status, str):
        replied = reply_status.strip().lower() in {"1", "true", "yes", "replied", "reply", "done"}
    else:
        replied = False

    return (
        (status in TERMINAL_STATUSES)
        or (followup_status in TERMINAL_FOLLOWUP_STATUSES)
        or replied
        or (reply_count > 0)
    )


def _is_initial_eligible(lead: Dict[str, Any]) -> bool:
    """Fresh leads that have never been contacted."""
    status          = _status_clean(lead)
    last_email_sent = lead.get("last_email_sent")
    followup_step   = _to_int(lead.get("followup_step"))

    return (
        (status in {"new", "pending", "not_contacted", ""})
        and (last_email_sent is None)
        and (followup_step == 0)
        and (not _is_terminal(lead))
    )


def _is_followup_eligible(lead: Dict[str, Any]) -> bool:
    """
    Eligible for follow-up if and only if:
    - status == 'sent'
    - reply_count == 0  (replies stop automation)
    - not in a terminal state
    - has received an initial email
    """
    status          = _status_clean(lead)
    last_email_sent = lead.get("last_email_sent")
    reply_count     = _to_int(lead.get("reply_count"))

    return (
        (status == "sent")
        and (last_email_sent is not None)
        and (reply_count == 0)
        and (not _is_terminal(lead))
    )


def _compute_followup_action(lead: Dict[str, Any]) -> Optional[str]:
    """
    Determine which follow-up template to send based on the state machine.
    Returns the template key or None if nothing should be sent.

    State machine:
      sent + no_open followup_status + open=0 + followup_open=0 + reply=0 → mark failed, send nothing
      sent + no_open followup_status + (open>=1 OR followup_open>=1) + reply=0 → followup_soft_open
      sent + soft_open followup_status + reply=0 → mark failed, send nothing
      sent + soft_open followup_status + reply>0 → completed, send nothing (reply already handled)
      sent + no followup_status + open=0 + reply=0 → followup_no_open
      sent + no followup_status + open>=1 + reply=0 → followup_soft_open
      reply>0 at any point → stop, mark completed
    """
    if not _is_followup_eligible(lead):
        return None

    open_count         = _to_int(lead.get("open_count"))
    followup_open_count = _to_int(lead.get("followup_open_count"))
    reply_count        = _to_int(lead.get("reply_count"))
    followup_status    = _followup_status_clean(lead)

    # Rule 7: reply always wins — stop everything
    if reply_count > 0:
        return None

    # Rule 3: no_open follow-up was sent, still no engagement → mark failed
    if (
        (followup_status == "no_open")
        and (open_count == 0)
        and (followup_open_count == 0)
        and (reply_count == 0)
    ):
        return "__mark_failed__"

    # Rule 4: no_open follow-up was sent, now there are opens → soft_open
    if (
        (followup_status == "no_open")
        and ((open_count >= 1) or (followup_open_count >= 1))
        and (reply_count == 0)
    ):
        return "followup_soft_open"

    # Rule 5: soft_open follow-up was sent, still no reply → mark failed
    if (
        (followup_status == "soft_open")
        and (reply_count == 0)
    ):
        return "__mark_failed__"

    # Rule 6: soft_open follow-up was sent, reply came in → completed (no send)
    if (
        (followup_status == "soft_open")
        and (reply_count > 0)
    ):
        return "__mark_completed__"

    # Rule 1: no prior follow-up, no opens → followup_no_open
    if (
        (not followup_status)
        and (open_count == 0)
        and (followup_open_count == 0)
        and (reply_count == 0)
    ):
        return "followup_no_open"

    # Rule 2: no prior follow-up, has opens, no reply → followup_soft_open
    if (
        (not followup_status)
        and (open_count >= 1)
        and (reply_count == 0)
    ):
        return "followup_soft_open"

    return None


def normalize_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
    lead_id    = lead.get("id") or lead.get("lead_id") or lead.get("uuid")
    first_name = lead.get("first_name")
    last_name  = lead.get("last_name")
    name       = " ".join(filter(None, [first_name, last_name])) or None
    metadata   = lead.get("metadata") or {}

    return {
        "id":                   lead_id,
        "name":                 name,
        "first_name":           first_name,
        "last_name":            last_name,
        "email":                lead.get("email"),
        "company":              lead.get("company"),
        "industry":             lead.get("industry"),
        "lead_source":          lead.get("lead_source"),
        "campaign_id":          lead.get("campaign_id"),
        "country":              lead.get("country"),
        "tech_stack":           lead.get("tech_stack") or metadata.get("tech_stack"),
        "pain_points":          lead.get("pain_points") or metadata.get("pain_points"),
        "automation_maturity":  lead.get("automation_maturity") or metadata.get("automation_maturity"),
        "status":               lead.get("status"),
        "followup_status":      lead.get("followup_status"),
        "followup_open_count":  _to_int(lead.get("followup_open_count", 0)),
        "last_followup_sent_at": lead.get("last_followup_sent_at"),
        "reply_status":         lead.get("reply_status"),
        "replied_at":           lead.get("replied_at"),
        "open_count":           _to_int(lead.get("open_count", 0)),
        "click_count":          _to_int(lead.get("click_count", 0)),
        "reply_count":          _to_int(lead.get("reply_count", 0)),
        "conversion_count":     _to_int(lead.get("conversion_count", 0)),
        "last_email_sent":      lead.get("last_email_sent"),
        "next_followup":        lead.get("next_followup"),
        "thread_id":            lead.get("thread_id"),
        "gmail_message_id":     lead.get("gmail_message_id"),
        "followup_step":        _to_int(lead.get("followup_step", 0)),
        "score":                lead.get("score"),
        "followup_action":      _compute_followup_action(lead),
        "raw":                  lead,
    }


def _filter_ready_leads(normalized: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
    mode = _normalize_text(mode) or "cold"

    if mode == "cold":
        return [
            lead for lead in normalized
            if (lead.get("email")) and (lead.get("id")) and (_is_initial_eligible(lead))
        ]

    if mode == "followups":
        # Only fetch leads that actually have an action to perform
        return [
            lead for lead in normalized
            if (
                lead.get("email")
                and lead.get("id")
                and _is_followup_eligible(lead)
                and lead.get("followup_action") is not None
            )
        ]

    # "all" = cold + followups with actions
    return [
        lead for lead in normalized
        if lead.get("email") and lead.get("id") and (
            _is_initial_eligible(lead)
            or (
                _is_followup_eligible(lead)
                and lead.get("followup_action") is not None
            )
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
    """
    mode:
      "cold"      -> initial outreach only
      "followups" -> status='sent', reply_count=0, not failed
      "all"       -> both
    """
    mode = _normalize_text(mode) or "cold"
    print(f"\n🚨 FETCHING LEADS (MODE: {mode.upper()})\n")

    query = supabase.table("outreach_leads").select("*")
    if campaign_id is not None:
        query = query.eq("campaign_id", campaign_id)

    # DB-side pre-filter for followups — never fetch failed or replied
    if mode == "followups":
        query = query.eq("status", "sent").eq("reply_count", 0)

    response = query.execute()
    leads    = response.data or []

    normalized = [normalize_lead(lead) for lead in leads]

    for l in normalized[:10]:
        print(
            f"DEBUG → id:{l['id']} | {l['email']} | status:{l['status']} | "
            f"followup_status:{l['followup_status']} | open:{l['open_count']} | "
            f"followup_open:{l['followup_open_count']} | reply:{l['reply_count']} | "
            f"action:{l['followup_action']}"
        )

    ready = _filter_ready_leads(normalized, mode)

    # Test mode
    if TEST_EMAIL:
        emails = {_normalize_text(l.get("email")) for l in ready}
        if TEST_EMAIL in emails:
            ready = [l for l in ready if _normalize_text(l.get("email")) == TEST_EMAIL]
            print(f"\n🧪 TEST MODE → filtering to: {TEST_EMAIL}\n")
        else:
            print(f"\n🚀 NORMAL MODE (test email not in ready set)\n")
    else:
        print("\n🚀 NORMAL MODE\n")

    if country:
        ready = [l for l in ready if l.get("country") == country]
    if tech_stack:
        ready = [l for l in ready if l.get("tech_stack") and tech_stack.lower() in str(l["tech_stack"]).lower()]
    if pain_point:
        ready = [l for l in ready if l.get("pain_points") and pain_point.lower() in str(l["pain_points"]).lower()]
    if automation_maturity:
        ready = [l for l in ready if l.get("automation_maturity") == automation_maturity]
    if min_score > 0:
        ready = [l for l in ready if _to_float(l.get("score"), 0.0) >= float(min_score)]

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
        )
    )
