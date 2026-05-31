# outreach_engine/tracking/engagement_tracking.py

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase

# ── Dedup windows from environment ───────────────────────────────────────────
OPEN_DEDUP_SECONDS  = int(os.getenv("OPEN_DEDUP_SECONDS", "900"))
CLICK_DEDUP_SECONDS = int(os.getenv("CLICK_DEDUP_SECONDS", "300"))

OPEN_CACHE:  Dict[str, float] = {}
CLICK_CACHE: Dict[str, float] = {}

# ── Bot UA patterns (kept in sync with pixel_server.py) ──────────────────────
BOT_UA_PATTERNS = [
    "googleimageproxy",
    "google image proxy",
    "googlebot",
    "google-apps-script",
    "google-read-aloud",
    "apis-google",
    "feedfetcher-google",
    "msnbot",
    "bingbot",
    "microsoft office",
    "ms-office",
    "outlook",
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


def _fingerprint(lead_id: int, campaign_id: int, event_type: str) -> str:
    day = _utc_now().date().isoformat()
    raw = f"{lead_id}:{campaign_id}:{event_type}:{day}"
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
                "click_count, status, followup_status, email_opened, followup_step"
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
    try:
        now     = _utc_now_iso()
        updates: Dict[str, Any] = {"last_updated": now}

        if event_type == "opened":
            # Bot UA guard — the only filter
            ua = (metadata or {}).get("user_agent") or ""
            if _is_bot_ua(ua):
                print(f"🚫 Open ignored (bot UA) → lead_id={lead_id}")
                return

            updates["email_opened"] = True
            if not row.get("email_opened"):
                updates["email_opened_at"] = now

            email_type    = (metadata or {}).get("email_type")
            followup_step = _safe_int(row.get("followup_step"))

            # FIX: Check followup_step > 0 instead of followup_status
            # This ensures follow-up opens keep counting even after status='completed'
            if email_type == "followup" or (email_type is None and followup_step > 0):
                updates["followup_open_count"] = _safe_int(row.get("followup_open_count")) + 1
                print(f"📬 followup_open_count++ → lead_id={lead_id} (step={followup_step})")
            else:
                updates["open_count"] = _safe_int(row.get("open_count")) + 1
                print(f"📬 open_count++ → lead_id={lead_id} (step={followup_step})")

        elif event_type == "clicked":
            updates["click_count"]  = _safe_int(row.get("click_count")) + 1
            updates["link_clicked"] = True

        supabase.table("outreach_leads").update(updates).eq("id", lead_id).execute()

    except Exception as e:
        print(f"⚠️ outreach_leads counter update failed: {e}")


def _update_system_lead_counters(
    system_lead_id: Optional[str],
    event_type: str,
) -> None:
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
        row     = res.data[0] if res.data else {}
        now     = _utc_now_iso()
        updates: Dict[str, Any] = {"updated_at": now}

        if event_type == "opened":
            updates["open_count"]      = _safe_int(row.get("open_count")) + 1
            updates["email_opened"]    = True
            updates["email_opened_at"] = now
        elif event_type == "clicked":
            updates["link_clicked"] = True

        supabase.table("leads").update(updates).eq("id", system_lead_id).execute()

    except Exception as e:
        print(f"⚠️ leads counter update failed: {e}")


def _update_crm_analytics(
    system_lead_id: Optional[str],
    event_type: str,
) -> None:
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
        row         = res.data[0] if res.data else {}
        now         = _utc_now_iso()
        emails_sent = _safe_int(row.get("emails_sent"))
        opens       = _safe_int(row.get("opens"))
        clicks      = _safe_int(row.get("clicks"))
        replies     = _safe_int(row.get("replies"))
        conversions = _safe_int(row.get("conversions"))

        if event_type == "opened":
            opens += 1
        elif event_type == "clicked":
            clicks += 1

        engagement_score = (
            emails_sent * 1
            + opens      * 2
            + clicks     * 3
            + replies    * 5
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
    now     = _utc_now_iso()
    payload: Dict[str, Any] = {
        "lead_id":    system_lead_id or str(lead_id),
        "campaign_id": campaign_id,
        "event_type": event_type,
        "timestamp":  now,
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
    payload = dict(metadata or {})
    payload.setdefault("channel", "email")
    payload.setdefault("source", "pixel")

    # Bot UA guard
    if event_type == "opened" and _is_bot_ua(payload.get("user_agent")):
        print(f"🚫 Open ignored (bot UA) → lead_id={lead_id}")
        return False

    # Dedup
    fingerprint = _fingerprint(lead_id, campaign_id, event_type)
    cache       = OPEN_CACHE if event_type == "opened" else CLICK_CACHE
    ttl         = OPEN_DEDUP_SECONDS if event_type == "opened" else CLICK_DEDUP_SECONDS

    if not _remember(cache, fingerprint, ttl):
        print(f"🧠 Duplicate {event_type} ignored → lead_id={lead_id}")
        return False

    outreach_row   = _resolve_outreach_lead(lead_id)
    email          = (outreach_row.get("email") or "").strip().lower() or None
    system_lead_id = _resolve_system_lead_id_from_email(email)

    _insert_lead_event(lead_id, system_lead_id, campaign_id, event_type, payload)
    _update_outreach_lead_counters(lead_id, event_type, outreach_row, payload)
    _update_system_lead_counters(system_lead_id, event_type)
    _update_crm_analytics(system_lead_id, event_type)

    print(f"📡 {event_type} signal recorded → lead_id={lead_id} | campaign_id={campaign_id}")
    return True


def handle_pixel_open(
    lead_id: int,
    campaign_id: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    return _record_event(lead_id, campaign_id, "opened", metadata)


def handle_pixel_click(
    lead_id: int,
    campaign_id: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    return _record_event(lead_id, campaign_id, "clicked", metadata)
