# outreach_engine/tracking/pixel_tracker.py
"""
Pixel Tracker — Signal Ingestion Only.

Responsibilities:
  - Log open events with timestamp + campaign_id
  - Log click events with timestamp + campaign_id
  - Deduplicate events (in-memory + fingerprint)
  - Update counters in outreach_leads + leads + crm_analytics
  - DO NOT trigger follow-up decisions directly
  - DO NOT change lead status (status is managed by lead_manager)

Rule:
  open  = signal only → log it, update counters, stop
  click = analytics only → log it, update counters, stop
  Neither event triggers follow-up logic here.
  event_router receives the signal and routes it appropriately.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase

# ---------------------------------------------------------------------------
# Deduplication cache (in-memory, per process)
# ---------------------------------------------------------------------------

OPEN_CACHE: Dict[str, float] = {}
CLICK_CACHE: Dict[str, float] = {}

OPEN_DEDUP_SECONDS = 900   # 15 minutes — same lead/campaign open = one event
CLICK_DEDUP_SECONDS = 300  # 5 minutes  — same lead/campaign/url click = one event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _fingerprint(lead_id: int, campaign_id: int, event_type: str, metadata: Optional[Dict[str, Any]]) -> str:
    """
    Build a deduplication key from lead, campaign, event type, user agent, and day.
    Same lead opening the same email twice on the same day = same fingerprint.
    """
    md = metadata or {}
    ua = (md.get("user_agent") or "").strip().lower()
    day = _utc_now().date().isoformat()
    raw = f"{lead_id}:{campaign_id}:{event_type}:{day}:{ua}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _remember(cache: Dict[str, float], key: str, ttl_seconds: int) -> bool:
    """
    Returns True if this is a new event (should be recorded).
    Returns False if it's a duplicate within the TTL window.
    """
    now_ts = _utc_now().timestamp()
    last_seen = cache.get(key)
    if last_seen is not None and (now_ts - last_seen) < ttl_seconds:
        return False
    cache[key] = now_ts
    return True


# ---------------------------------------------------------------------------
# DB operations — counters only, no status changes
# ---------------------------------------------------------------------------

def _resolve_outreach_lead(lead_id: int) -> Dict[str, Any]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("id, email, campaign_id, open_count, click_count, status")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    except Exception as e:
        print(f"⚠️ outreach lead lookup failed: {e}")
    return {}


def _resolve_system_lead_id_from_email(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    try:
        res = (
            supabase.table("leads")
            .select("id")
            .ilike("email", email.strip().lower())
            .limit(1)
            .execute()
        )
        if res.data:
            lead_id = res.data[0].get("id")
            return str(lead_id) if lead_id else None
    except Exception as e:
        print(f"⚠️ system lead lookup failed: {e}")
    return None


def _update_outreach_lead_counters(lead_id: int, event_type: str) -> None:
    """
    Increment open_count or click_count.
    Do NOT change lead status here — that belongs to lead_manager.
    """
    try:
        row = _resolve_outreach_lead(lead_id)
        if not row:
            return

        now = _utc_now_iso()
        updates: Dict[str, Any] = {"last_updated": now}

        if event_type == "opened":
            updates["open_count"] = _safe_int(row.get("open_count")) + 1
            updates["email_opened"] = True
            updates["email_opened_at"] = now

        elif event_type == "clicked":
            updates["click_count"] = _safe_int(row.get("click_count")) + 1
            updates["link_clicked"] = True
            updates["link_clicked_at"] = now

        supabase.table("outreach_leads").update(updates).eq("id", lead_id).execute()

    except Exception as e:
        print(f"⚠️ outreach_leads counter update failed: {e}")


def _update_system_lead_counters(system_lead_id: Optional[str], event_type: str) -> None:
    """Update open/click counters on the main leads table."""
    if not system_lead_id:
        return
    try:
        res = (
            supabase.table("leads")
            .select("open_count")
            .eq("id", system_lead_id)
            .limit(1)
            .execute()
        )
        row = res.data[0] if res.data else {}
        now = _utc_now_iso()
        updates: Dict[str, Any] = {"updated_at": now}

        if event_type == "opened":
            updates["open_count"] = _safe_int(row.get("open_count")) + 1
            updates["email_opened"] = True
            updates["email_opened_at"] = now

        elif event_type == "clicked":
            updates["link_clicked"] = True
            updates["link_clicked_at"] = now

        supabase.table("leads").update(updates).eq("id", system_lead_id).execute()

    except Exception as e:
        print(f"⚠️ leads counter update failed: {e}")


def _update_crm_analytics(system_lead_id: Optional[str], event_type: str) -> None:
    """Upsert engagement counters + recalculate engagement_score."""
    if not system_lead_id:
        return
    try:
        res = (
            supabase.table("crm_analytics")
            .select("emails_sent, opens, clicks, replies, conversions, engagement_score")
            .eq("lead_id", system_lead_id)
            .limit(1)
            .execute()
        )
        row = res.data[0] if res.data else {}
        now = _utc_now_iso()

        emails_sent = _safe_int(row.get("emails_sent"))
        opens = _safe_int(row.get("opens"))
        clicks = _safe_int(row.get("clicks"))
        replies = _safe_int(row.get("replies"))
        conversions = _safe_int(row.get("conversions"))

        if event_type == "opened":
            opens += 1
        elif event_type == "clicked":
            clicks += 1

        engagement_score = (
            emails_sent * 1
            + opens * 2
            + clicks * 3
            + replies * 5
            + conversions * 10
        )

        payload = {
            "lead_id": system_lead_id,
            "emails_sent": emails_sent,
            "opens": opens,
            "clicks": clicks,
            "replies": replies,
            "conversions": conversions,
            "engagement_score": engagement_score,
            "last_activity": now,
        }

        supabase.table("crm_analytics").upsert(payload).execute()

    except Exception as e:
        print(f"⚠️ crm_analytics update failed: {e}")


def _insert_lead_event(
    lead_id: int,
    system_lead_id: Optional[str],
    campaign_id: int,
    event_type: str,
    metadata: Dict[str, Any],
) -> None:
    """Insert a raw event row into lead_events."""
    now = _utc_now_iso()
    payload: Dict[str, Any] = {
        "lead_id": system_lead_id or str(lead_id),
        "campaign_id": campaign_id,
        "event_type": event_type,
        "timestamp": now,
        "metadata": {
            **metadata,
            "outreach_lead_id": lead_id,
            "campaign_id": campaign_id,
            "timestamp": now,
        },
    }
    try:
        supabase.table("lead_events").insert(payload).execute()
    except Exception as e:
        msg = str(e).lower()
        if "campaign_id" in msg or "schema cache" in msg or "does not exist" in msg:
            fallback = dict(payload)
            fallback.pop("campaign_id", None)
            supabase.table("lead_events").insert(fallback).execute()
        else:
            print(f"⚠️ lead_events insert failed: {e}")


# ---------------------------------------------------------------------------
# Core record function
# ---------------------------------------------------------------------------

def _record_event(
    lead_id: int,
    campaign_id: int,
    event_type: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Record an open or click event.

    Steps:
      1. Deduplicate (in-memory fingerprint)
      2. Insert lead_event row
      3. Update counters (outreach_leads, leads, crm_analytics)
      4. Return True if event was recorded, False if duplicate

    No follow-up routing happens here.
    """
    payload = dict(metadata or {})
    payload.setdefault("channel", "email")
    payload.setdefault("source", "pixel")

    fingerprint = _fingerprint(lead_id, campaign_id, event_type, payload)
    cache = OPEN_CACHE if event_type == "opened" else CLICK_CACHE
    ttl = OPEN_DEDUP_SECONDS if event_type == "opened" else CLICK_DEDUP_SECONDS

    if not _remember(cache, fingerprint, ttl):
        print(f"🧠 Duplicate {event_type} ignored (cache) → lead_id={lead_id}")
        return False

    outreach_row = _resolve_outreach_lead(lead_id)
    email = (outreach_row.get("email") or "").strip().lower() or None
    system_lead_id = _resolve_system_lead_id_from_email(email)

    _insert_lead_event(lead_id, system_lead_id, campaign_id, event_type, payload)
    _update_outreach_lead_counters(lead_id, event_type)
    _update_system_lead_counters(system_lead_id, event_type)
    _update_crm_analytics(system_lead_id, event_type)

    print(f"📡 {event_type} signal recorded → lead_id={lead_id} | campaign_id={campaign_id}")
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def handle_pixel_open(
    lead_id: int,
    campaign_id: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Record an email open signal.
    Does NOT trigger follow-up logic — use event_router for that.
    """
    return _record_event(lead_id, campaign_id, "opened", metadata)


def handle_pixel_click(
    lead_id: int,
    campaign_id: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Record a link click signal.
    Clicks are analytics only — no follow-up routing is triggered.
    """
    return _record_event(lead_id, campaign_id, "clicked", metadata)
