# outreach_engine/core/safety_gurad.py

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from outreach_engine.database.supabase_client import supabase


MAX_EMAILS_PER_LEAD_PER_DAY = 1
DOMAIN_COOLDOWN_HOURS = 24
STOP_IF_REPLIED = True
BOUNCE_COMPONENT = "email_bounce"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _email_domain(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.split("@", 1)[1].strip()


def _domain_from_website(website: Optional[str]) -> str:
    if not website:
        return ""
    try:
        parsed = urlparse(website if website.startswith(("http://", "https://")) else f"https://{website}")
        host = (parsed.netloc or "").lower().strip()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def _lead_status(lead_id: Any) -> str:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("status")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return (res.data[0].get("status") or "").strip().lower()
    except Exception:
        pass
    return ""


def _count_sent_today_for_lead(lead_id: Any) -> int:
    """
    Counts email 'sent' events today for a lead using lead_events.
    """
    try:
        start_of_day = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        res = (
            supabase.table("lead_events")
            .select("id, timestamp, event_type")
            .eq("lead_id", lead_id)
            .eq("event_type", "sent")
            .gte("timestamp", start_of_day.isoformat())
            .execute()
        )
        return len(res.data or [])
    except Exception:
        return 0


def _last_domain_send_time(domain: str) -> Optional[datetime]:
    """
    Finds the most recent last_email_sent across outreach_leads with the same email domain.
    """
    if not domain:
        return None

    try:
        res = (
            supabase.table("outreach_leads")
            .select("last_email_sent, email")
            .ilike("email", f"%@{domain}")
            .order("last_email_sent", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            return _parse_dt(res.data[0].get("last_email_sent"))
    except Exception:
        pass

    return None


def _is_bounced(email: str) -> bool:
    """
    Checks if an email is already blacklisted/bounced.
    Uses system_failures as a persistence layer.
    """
    if not email:
        return False

    try:
        res = (
            supabase.table("system_failures")
            .select("id, component, error_message, failure_reason")
            .eq("component", BOUNCE_COMPONENT)
            .ilike("error_message", email)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception:
        return False


def _mark_bounced(email: str, reason: str = "bounced") -> None:
    """
    Persist bounce info using system_failures.
    """
    try:
        supabase.table("system_failures").insert({
            "component": BOUNCE_COMPONENT,
            "error_message": email,
            "failure_reason": reason,
            "retry_count": 0,
            "last_retry": _utcnow_iso(),
            "created_at": _utcnow_iso(),
        }).execute()
    except Exception:
        pass


def _lead_responded(lead_id: Any) -> bool:
    status = _lead_status(lead_id)
    return status in {"replied", "interested", "converted", "opt-out", "unsubscribed", "completed"}


def can_send_to_lead(
    lead_id: Any,
    email: str,
    website: Optional[str] = None,
    max_per_day: int = MAX_EMAILS_PER_LEAD_PER_DAY,
    domain_cooldown_hours: int = DOMAIN_COOLDOWN_HOURS,
) -> Tuple[bool, str]:
    """
    Returns:
      (allowed, reason)
    """
    if not lead_id or not email:
        return False, "missing_required_fields"

    if STOP_IF_REPLIED and _lead_responded(lead_id):
        return False, "lead_already_replied_or_closed"

    if _is_bounced(email):
        return False, "email_bounced_blacklisted"

    sent_today = _count_sent_today_for_lead(lead_id)
    if sent_today >= max_per_day:
        return False, "lead_daily_limit_reached"

    domain = _email_domain(email)
    if not domain and website:
        domain = _domain_from_website(website)

    if domain and domain_cooldown_hours > 0:
        last_send = _last_domain_send_time(domain)
        if last_send:
            now = _utcnow()
            if last_send.tzinfo is None:
                last_send = last_send.replace(tzinfo=timezone.utc)
            if now - last_send < timedelta(hours=domain_cooldown_hours):
                return False, "domain_cooldown_active"

    return True, "allowed"


def guard_send(
    lead_id: Any,
    email: str,
    website: Optional[str] = None,
    max_per_day: int = MAX_EMAILS_PER_LEAD_PER_DAY,
    domain_cooldown_hours: int = DOMAIN_COOLDOWN_HOURS,
) -> bool:
    allowed, _ = can_send_to_lead(
        lead_id=lead_id,
        email=email,
        website=website,
        max_per_day=max_per_day,
        domain_cooldown_hours=domain_cooldown_hours,
    )
    return allowed


def record_bounce(email: str, reason: str = "bounced") -> None:
    _mark_bounced(email, reason=reason)


def should_stop_for_reply(lead_id: Any) -> bool:
    return _lead_responded(lead_id)
