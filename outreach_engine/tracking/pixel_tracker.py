from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase

OPEN_CACHE: Dict[str, float] = {}
CLICK_CACHE: Dict[str, float] = {}

OPEN_DEDUP_SECONDS = 900
CLICK_DEDUP_SECONDS = 300


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
    md = metadata or {}
    ua = (md.get("user_agent") or "").strip().lower()
    day = _utc_now().date().isoformat()
    raw = f"{lead_id}:{campaign_id}:{event_type}:{day}:{ua}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _remember(cache: Dict[str, float], key: str, ttl_seconds: int) -> bool:
    now_ts = _utc_now().timestamp()
    last_seen = cache.get(key)
    if last_seen is not None and (now_ts - last_seen) < ttl_seconds:
        return False
    cache[key] = now_ts
    return True


def _safe_insert_lead_event(payload: Dict[str, Any]) -> None:
    try:
        supabase.table("lead_events").insert(payload).execute()
        return
    except Exception as e:
        msg = str(e).lower()
        if "campaign_id" in msg or "does not exist" in msg or "schema cache" in msg:
            fallback = dict(payload)
            fallback.pop("campaign_id", None)
            supabase.table("lead_events").insert(fallback).execute()
            return
        raise


def _resolve_outreach_lead(lead_id: int) -> Dict[str, Any]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("id, email, campaign_id, open_count, click_count")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    except Exception as e:
        print(f"⚠ outreach lead lookup failed: {e}")
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
        print(f"⚠ system lead lookup failed: {e}")

    return None


def _update_outreach_leads(lead_id: int, event_type: str) -> None:
    try:
        row = _resolve_outreach_lead(lead_id)
        now = _utc_now_iso()

        updates: Dict[str, Any] = {"last_updated": now}

        if event_type == "opened":
            updates["open_count"] = _safe_int(row.get("open_count")) + 1
            updates["status"] = "sent" if (row.get("status") or "").lower() in {"pending", "new"} else row.get("status") or "sent"
        elif event_type == "clicked":
            updates["click_count"] = _safe_int(row.get("click_count")) + 1
            updates["status"] = "sent" if (row.get("status") or "").lower() in {"pending", "new"} else row.get("status") or "sent"

        supabase.table("outreach_leads").update(updates).eq("id", lead_id).execute()
    except Exception as e:
        print(f"⚠ outreach_leads sync failed: {e}")


def _update_system_leads(system_lead_id: Optional[str], event_type: str) -> None:
    if not system_lead_id:
        return

    try:
        res = (
            supabase.table("leads")
            .select("open_count, reply_count, email_opened, link_clicked")
            .eq("id", system_lead_id)
            .limit(1)
            .execute()
        )

        row = res.data[0] if res.data else {}
        now = _utc_now_iso()

        updates: Dict[str, Any] = {"updated_at": now}

        if event_type == "opened":
            updates.update({
                "open_count": _safe_int(row.get("open_count")) + 1,
                "email_opened": True,
                "email_opened_at": now,
            })
        elif event_type == "clicked":
            updates.update({
                "link_clicked": True,
                "link_clicked_at": now,
            })

        supabase.table("leads").update(updates).eq("id", system_lead_id).execute()

    except Exception as e:
        print(f"⚠ leads sync failed: {e}")


def _update_crm_analytics(system_lead_id: Optional[str], event_type: str) -> None:
    if not system_lead_id:
        return

    try:
        res = (
            supabase.table("crm_analytics")
            .select("engagement_score, emails_sent, opens, clicks, replies, conversions, last_activity")
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

        engagement_score = emails_sent * 1 + opens * 2 + clicks * 3 + replies * 5 + conversions * 10

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
        print(f"⚠ crm_analytics sync failed: {e}")


def _record_event(
    lead_id: int,
    campaign_id: int,
    event_type: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    payload = dict(metadata or {})
    payload.setdefault("channel", "email")
    payload.setdefault("source", "pixel")

    fingerprint = _fingerprint(lead_id, campaign_id, event_type, payload)
    cache = OPEN_CACHE if event_type == "opened" else CLICK_CACHE
    ttl = OPEN_DEDUP_SECONDS if event_type == "opened" else CLICK_DEDUP_SECONDS

    if not _remember(cache, fingerprint, ttl):
        return False

    outreach_row = _resolve_outreach_lead(lead_id)
    email = (outreach_row.get("email") or "").strip().lower() or None
    system_lead_id = _resolve_system_lead_id_from_email(email)

    now = _utc_now_iso()

    try:
        _safe_insert_lead_event(
            {
                "lead_id": system_lead_id or str(lead_id),
                "campaign_id": campaign_id,
                "event_type": event_type,
                "timestamp": now,
                "metadata": {
                    **payload,
                    "campaign_id": campaign_id,
                    "outreach_lead_id": lead_id,
                    "timestamp": now,
                },
            }
        )
    except Exception as e:
        print(f"⚠ lead_events insert failed: {e}")

    _update_outreach_leads(lead_id, event_type)
    _update_system_leads(system_lead_id, event_type)
    _update_crm_analytics(system_lead_id, event_type)

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
