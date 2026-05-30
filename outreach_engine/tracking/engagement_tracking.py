# outreach_engine/tracking/engagement_tracking.py

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase

# ── Dedup windows + send guard from environment ───────────────────────────────
OPEN_DEDUP_SECONDS       = int(os.getenv("OPEN_DEDUP_SECONDS",       "900"))
CLICK_DEDUP_SECONDS      = int(os.getenv("CLICK_DEDUP_SECONDS",      "300"))
# 2 seconds catches prefetch-on-send without blocking fast real opens.
# The old pixel_tracker had this at 300s (5 min) which was far too aggressive.
MIN_SEND_TO_OPEN_SECONDS = int(os.getenv("MIN_SEND_TO_OPEN_SECONDS", "2"))

OPEN_CACHE:  Dict[str, float] = {}
CLICK_CACHE: Dict[str, float] = {}

# ── Bot UA patterns ───────────────────────────────────────────────────────────
# "googleimageproxy" is intentionally absent — Gmail proxies real human opens
# through it.  The 2-second send guard separates prefetch from real opens.
BOT_UA_PATTERNS = [
    "googlebot",
    "google-apps-script",
    "google-read-aloud",
    "apis-google",
    "feedfetcher-google",
    "msnbot",
    "bingbot",
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
    """
    Returns True for known crawler / scanner UAs.
    Empty UA → treated as bot (no legitimate email client omits UA).
    """
    if not user_agent:
        return True
    ua = user_agent.lower().strip()
    return any(pattern in ua for pattern in BOT_UA_PATTERNS)


def _is_too_soon_after_send(row: Dict[str, Any], lead_id: int) -> bool:
    """
    Returns True if the open arrived within MIN_SEND_TO_OPEN_SECONDS of send.
    Returns False (allow) when last_email_sent is missing.
    """
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
    """
    Per-send dedup key.
    cold vs follow-up and follow-up #1 vs #2 each get distinct keys.
    Falls back to day bucket when last_email_sent is absent.
    """
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


def _update_outreach_lead_counters(
    lead_id: int,
    event_type: str,
    row: Dict[str, Any],
    email_type: Optional[str] = None,
) -> None:
    """
    Increment the correct counter.
    Bot-UA and send-guard checks are done in _record_event — not repeated here.
    """
    try:
        now     = _utc_now_iso()
        updates: Dict[str, Any] = {"last_updated": now}

        if event_type == "opened":
            followup_status = (row.get("followup_status") or "").strip().lower()
            updates["email_opened"] = True

            if not row.get("email_opened"):
                updates["email_opened_at"] = now

            # Explicit email_type takes priority over followup_status
            if email_type == "followup" or (
                email_type is None and followup_status in {"no_open", "soft_open"}
            ):
                updates["followup_open_count"] = _safe_int(row.get("followup_open_count")) + 1
                print(
                    f"📬 followup_open_count++ → lead_id={lead_id} "
                    f"(email_type={email_type}, followup_status={followup_status})"
                )
            else:
                updates["open_count"] = _safe_int(row.get("open_count")) + 1
                print(f"📬 open_count++ → lead_id={lead_id}")

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
    payload = dict(metadata or {})
    payload.setdefault("channel", "email")
    payload.setdefault("source", "pixel")

    email_type = payload.get("email_type")

    # ── Gate 1: Bot UA (single check — not repeated in counter helpers) ───
    if event_type == "opened" and _is_bot_ua(payload.get("user_agent")):
        print(f"🚫 Open ignored (bot UA) → lead_id={lead_id}")
        return False

    # ── Resolve lead row first (needed for send-guard + fingerprint) ──────
    outreach_row    = _resolve_outreach_lead(lead_id)
    last_email_sent = outreach_row.get("last_email_sent")

    # ── Gate 2: Send guard ────────────────────────────────────────────────
    if event_type == "opened" and _is_too_soon_after_send(outreach_row, lead_id):
        return False

    # ── Gate 3: Per-send dedup ────────────────────────────────────────────
    fingerprint = _fingerprint(lead_id, campaign_id, event_type, last_email_sent, email_type)
    cache       = OPEN_CACHE if event_type == "opened" else CLICK_CACHE
    ttl         = OPEN_DEDUP_SECONDS if event_type == "opened" else CLICK_DEDUP_SECONDS

    if not _remember(cache, fingerprint, ttl):
        print(
            f"🧠 Duplicate {event_type} ignored → lead_id={lead_id} "
            f"(email_type={email_type})"
        )
        return False

    # ── Persist ───────────────────────────────────────────────────────────
    email          = (outreach_row.get("email") or "").strip().lower() or None
    system_lead_id = _resolve_system_lead_id_from_email(email)

    _insert_lead_event(lead_id, system_lead_id, campaign_id, event_type, payload)
    _update_outreach_lead_counters(lead_id, event_type, outreach_row, email_type)
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
