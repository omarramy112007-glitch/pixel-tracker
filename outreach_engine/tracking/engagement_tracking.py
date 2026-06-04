# outreach_engine/tracking/engagement_tracking.py

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase

OPEN_DEDUP_SECONDS       = int(os.getenv("OPEN_DEDUP_SECONDS",       "2"))
CLICK_DEDUP_SECONDS      = int(os.getenv("CLICK_DEDUP_SECONDS",      "300"))
MIN_SEND_TO_OPEN_SECONDS = int(os.getenv("MIN_SEND_TO_OPEN_SECONDS", "2"))

OPEN_CACHE:  Dict[str, float] = {}
CLICK_CACHE: Dict[str, float] = {}

BOT_UA_PATTERNS = [
    "googlebot",
    "google-apps-script",
    "google-read-aloud",
    "apis-google",
    "feedfetcher-google",
    "msnbot",
    "bingbot",
    "microsoft office",
    "ms-office",
    "safelinks",
    "applebot",
    "apple mail privacy",
    "barracudacentral",
    "proofpoint",
    "mimecast",
    "symantec",
    "sophos",
    "trend micro",
    "cloudmark",
    "spamhaus",
    "postfix",
    "wget",
    "curl",
    "python-requests",
    "python-httpx",
    "libwww",
    "jakarta",
    "apache-httpclient",
    "java/",
    "go-http-client",
    "ruby",
    "scrapy",
    "phantomjs",
    "headlesschrome",
    "prerender",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _is_bot_ua(user_agent: Optional[str]) -> bool:
    if not user_agent:
        return True
    ua = user_agent.lower().strip()
    return any(pattern in ua for pattern in BOT_UA_PATTERNS)


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
                "click_count, status, followup_status, email_opened, "
                "last_email_sent, followup_step"
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


def _update_outreach_lead_counters(
    lead_id: int,
    event_type: str,
    row: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Opens: absolute no-op.
    pixel_server._track_open_db() owns ALL open writes:
        - outreach_leads.open_count
        - outreach_leads.followup_open_count
        - outreach_leads.email_opened
        - outreach_leads.email_opened_at
        - leads.open_count
        - leads.email_opened
        - leads.email_opened_at
        - crm_analytics.opens
        - lead_events (open row)

    Clicks: handled here only if called from a non-pixel-server path.
    pixel_server._track_click_db() handles outreach_leads click writes
    when the pixel fires.
    """
    try:
        now = _utc_now_iso()

        if event_type == "opened":
            print(
                f"⏭️ engagement_tracking: open skipped entirely "
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
    Opens: absolute no-op.
    pixel_server._update_system_lead_open() owns leads table open writes.

    Clicks: update leads.link_clicked only if called from a
    non-pixel-server path.
    """
    if not system_lead_id:
        return

    if event_type == "opened":
        print(
            f"⏭️ engagement_tracking: leads table open skipped "
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
    Opens: absolute no-op.
    pixel_server._update_crm_analytics() owns crm_analytics open writes.

    Clicks: update only if called from a non-pixel-server path.
    """
    if not system_lead_id:
        return

    if event_type == "opened":
        print(
            f"⏭️ engagement_tracking: crm_analytics open skipped "
            f"→ system_lead_id={system_lead_id} "
            f"(pixel_server is sole owner)"
        )
        return

    try:
        res = (
            supabase.table("crm_analytics")
            .select(
                "emails_sent, opens, clicks, replies, "
                "conversions, engagement_score"
            )
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
    Opens: absolute no-op.
    pixel_server._record_lead_event() inserts the open row.
    Inserting here creates a duplicate.

    Clicks: insert only if called from a non-pixel-server path.
    """
    if event_type == "opened":
        print(
            f"⏭️ engagement_tracking: lead_events open insert skipped "
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
    Opens: complete no-op gate at the top level.
    pixel_server._handle_open() is the canonical entry point for open
    tracking. If this function is called with event_type="opened" it
    means either:
      (a) it was called redundantly alongside pixel_server — return False
          to prevent any double-write.
      (b) it was called without a pixel fire — still return False because
          without a pixel fire the open was not real or not verifiable.
    """
    if event_type == "opened":
        print(
            f"⏭️ engagement_tracking._record_event: open ignored entirely "
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
    _update_outreach_lead_counters(lead_id, event_type, outreach_row, payload)
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
    Disabled. pixel_server._handle_open() is the sole open tracking path.
    Calling this is always a no-op.
    """
    print(
        f"⏭️ engagement_tracking.handle_pixel_open: no-op "
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
