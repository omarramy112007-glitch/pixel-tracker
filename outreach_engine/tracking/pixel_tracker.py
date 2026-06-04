# outreach_engine/tracking/pixel_tracker.py

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase

OPEN_CACHE:  Dict[str, float] = {}
CLICK_CACHE: Dict[str, float] = {}

OPEN_DEDUP_SECONDS       = int(os.getenv("OPEN_DEDUP_SECONDS",       "2"))
CLICK_DEDUP_SECONDS      = int(os.getenv("CLICK_DEDUP_SECONDS",      "300"))
MIN_SEND_TO_OPEN_SECONDS = int(os.getenv("MIN_SEND_TO_OPEN_SECONDS", "2"))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _fingerprint(
    lead_id: int,
    campaign_id: int,
    event_type: str,
    last_email_sent: Optional[str] = None,
    email_type: Optional[str] = None,
) -> str:
    day  = _utc_now().date().isoformat()
    sent = str(last_email_sent).strip() if last_email_sent else day
    et   = (email_type or "none").strip().lower()
    raw  = f"{lead_id}:{campaign_id}:{event_type}:{et}:{sent}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _remember(cache: Dict[str, float], key: str, ttl_seconds: int) -> bool:
    now_ts    = _utc_now().timestamp()
    last_seen = cache.get(key)
    if last_seen is not None and (now_ts - last_seen) < ttl_seconds:
        return False
    cache[key] = now_ts
    return True


def _resolve_outreach_lead(lead_id: int) -> Dict[str, Any]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select(
                "id, email, campaign_id, open_count, followup_open_count, "
                "click_count, status, followup_status, email_opened, last_email_sent"
            )
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


def _is_too_soon_after_send(row: Dict[str, Any], lead_id: int) -> bool:
    last_email_sent = row.get("last_email_sent")
    if not last_email_sent:
        return False
    try:
        sent_time = datetime.fromisoformat(str(last_email_sent).replace("Z", "+00:00"))
        if sent_time.tzinfo is None:
            sent_time = sent_time.replace(tzinfo=timezone.utc)
        elapsed = (_utc_now() - sent_time).total_seconds()
        if elapsed < MIN_SEND_TO_OPEN_SECONDS:
            print(
                f"⏳ Open ignored (too soon: {elapsed:.1f}s < "
                f"{MIN_SEND_TO_OPEN_SECONDS}s) → lead_id={lead_id}"
            )
            return True
    except Exception as e:
        print(f"⚠️ Time check failed for lead {lead_id}: {e}")
    return False


def _update_outreach_lead_counters(
    lead_id: int,
    event_type: str,
    row: Dict[str, Any],
    email_type: Optional[str] = None,
) -> None:
    """
    Opens: complete no-op. 
    pixel_server._track_open_db() owns ALL open writes across ALL tables.

    Clicks: update outreach_leads.click_count only if pixel_server has
    not already done so via _track_click_db().
    NOTE: pixel_server._track_click_db() now handles click writes too,
    so this function should only be reached if pixel_tracker is called
    from a non-pixel-server code path (e.g. a direct API call).
    """
    try:
        now = _utc_now_iso()

        if event_type == "opened":
            # Absolute no-op — pixel_server is sole owner.
            print(
                f"⏭️ pixel_tracker: open skipped entirely "
                f"→ lead_id={lead_id} "
                f"(pixel_server._track_open_db is sole owner of all open writes)"
            )
            return

        elif event_type == "clicked":
            fresh = (
                supabase.table("outreach_leads")
                .select("click_count")
                .eq("id", lead_id)
                .limit(1)
                .execute()
            )
            live_row = fresh.data[0] if fresh.data else row
            updates: Dict[str, Any] = {
                "click_count":  _safe_int(live_row.get("click_count")) + 1,
                "link_clicked": True,
                "last_updated": now,
            }
            supabase.table("outreach_leads").update(updates).eq(
                "id", lead_id
            ).execute()

        else:
            return

    except Exception as e:
        print(f"⚠️ outreach_leads counter update failed: {e}")


def _update_system_lead_counters(
    system_lead_id: Optional[str],
    event_type: str,
) -> None:
    """
    Opens: complete no-op.
    pixel_server._update_system_lead_open() owns leads table open writes.

    Clicks: update leads.link_clicked only.
    """
    if not system_lead_id:
        return

    if event_type == "opened":
        # No-op — pixel_server._update_system_lead_open() handles this.
        print(
            f"⏭️ pixel_tracker: leads table open skipped "
            f"→ system_lead_id={system_lead_id} "
            f"(pixel_server is sole owner)"
        )
        return

    try:
        now = _utc_now_iso()
        updates: Dict[str, Any] = {"updated_at": now}
        if event_type == "clicked":
            updates["link_clicked"] = True
        supabase.table("leads").update(updates).eq(
            "id", system_lead_id
        ).execute()
    except Exception as e:
        print(f"⚠️ leads counter update failed: {e}")


def _update_crm_analytics(
    system_lead_id: Optional[str],
    event_type: str,
) -> None:
    """
    Opens: complete no-op.
    pixel_server._update_crm_analytics() owns crm_analytics open writes.

    Clicks: update only if called from a non-pixel-server path.
    """
    if not system_lead_id:
        return

    if event_type == "opened":
        # No-op — pixel_server._update_crm_analytics() handles this.
        print(
            f"⏭️ pixel_tracker: crm_analytics open skipped "
            f"→ system_lead_id={system_lead_id} "
            f"(pixel_server is sole owner)"
        )
        return

    try:
        res = (
            supabase.table("crm_analytics")
            .select("emails_sent, opens, clicks, replies, conversions, engagement_score")
            .eq("lead_id", system_lead_id)
            .limit(1)
            .execute()
        )
        row         = res.data[0] if res.data else {}
        now         = _utc_now_iso()
        emails_sent = _safe_int(row.get("emails_sent"))
        opens       = _safe_int(row.get("opens"))
        clicks      = _safe_int(row.get("clicks"))
        replies     = _safe_int(row.get("replies"))
        conversions = _safe_int(row.get("conversions"))

        if event_type == "clicked":
            clicks += 1

        engagement_score = (
            emails_sent * 1
            + opens     * 2
            + clicks    * 3
            + replies   * 5
            + conversions * 10
        )

        supabase.table("crm_analytics").upsert({
            "lead_id":          system_lead_id,
            "emails_sent":      emails_sent,
            "opens":            opens,
            "clicks":           clicks,
            "replies":          replies,
            "conversions":      conversions,
            "engagement_score": engagement_score,
            "last_activity":    now,
        }).execute()

    except Exception as e:
        print(f"⚠️ crm_analytics update failed: {e}")


def _insert_lead_event(
    lead_id: int,
    system_lead_id: Optional[str],
    campaign_id: int,
    event_type: str,
    metadata: Dict[str, Any],
) -> None:
    """
    Opens: complete no-op.
    pixel_server._record_lead_event() is the sole inserter for open events.
    Inserting here too would create a duplicate lead_events row.

    Clicks: insert only if called from a non-pixel-server path.
    """
    if event_type == "opened":
        # No-op — pixel_server._record_lead_event() handles this.
        print(
            f"⏭️ pixel_tracker: lead_events open insert skipped "
            f"→ lead_id={lead_id} "
            f"(pixel_server is sole owner of open event inserts)"
        )
        return

    now     = _utc_now_iso()
    payload: Dict[str, Any] = {
        "lead_id":     system_lead_id or str(lead_id),
        "campaign_id": campaign_id,
        "event_type":  event_type,
        "timestamp":   now,
        "metadata": {
            **metadata,
            "outreach_lead_id": lead_id,
            "campaign_id":      campaign_id,
            "timestamp":        now,
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


def _record_event(
    lead_id: int,
    campaign_id: int,
    event_type: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    For opens: this function is called from a non-pixel-server path
    (e.g. a manual API trigger).  Because pixel_server._handle_open()
    owns all open tracking when a pixel fires, this function must be a
    complete no-op for opens to prevent double-counting.

    If this function IS the only caller (no pixel fired), that means
    the open was not tracked via pixel at all, in which case
    pixel_server._track_open_db() was never called and we should
    still skip here — the canonical tracking path is the pixel.
    """
    if event_type == "opened":
        print(
            f"⏭️ pixel_tracker._record_event: open ignored entirely "
            f"→ lead_id={lead_id} "
            f"(pixel_server is sole owner of open tracking)"
        )
        return False

    payload = dict(metadata or {})
    payload.setdefault("channel", "email")
    payload.setdefault("source", "pixel")

    email_type = payload.get("email_type")

    outreach_row    = _resolve_outreach_lead(lead_id)
    last_email_sent = outreach_row.get("last_email_sent")

    fingerprint = _fingerprint(
        lead_id, campaign_id, event_type, last_email_sent, email_type
    )
    cache = CLICK_CACHE
    ttl   = CLICK_DEDUP_SECONDS

    if not _remember(cache, fingerprint, ttl):
        print(
            f"🧠 Duplicate {event_type} ignored (burst) → lead_id={lead_id} "
            f"(email_type={email_type})"
        )
        return False

    email          = (outreach_row.get("email") or "").strip().lower() or None
    system_lead_id = _resolve_system_lead_id_from_email(email)

    _insert_lead_event(lead_id, system_lead_id, campaign_id, event_type, payload)
    _update_outreach_lead_counters(lead_id, event_type, outreach_row, email_type)
    _update_system_lead_counters(system_lead_id, event_type)
    _update_crm_analytics(system_lead_id, event_type)

    print(f"📡 {event_type} recorded → lead_id={lead_id} | campaign_id={campaign_id}")
    return True


def handle_pixel_open(
    lead_id: int,
    campaign_id: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Open tracking via pixel_tracker is disabled.
    pixel_server._handle_open() → _track_open_db() is the sole path.
    Calling this function is a no-op and returns False.
    """
    print(
        f"⏭️ pixel_tracker.handle_pixel_open: no-op "
        f"→ lead_id={lead_id} "
        f"(pixel_server owns all open tracking)"
    )
    return False


def handle_pixel_click(
    lead_id: int,
    campaign_id: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    return _record_event(lead_id, campaign_id, "clicked", metadata)
