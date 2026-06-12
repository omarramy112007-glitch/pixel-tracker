# outreach_engine/tracking/event_repository.py

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from outreach_engine.database.supabase_client import supabase

CRM_EVENT_FIELD_MAP = {
    "sent":      "emails_sent",
    "opened":    "opens",
    "clicked":   "clicks",
    "replied":   "replies",
    "converted": "conversions",
}

CAMPAIGN_EVENT_FIELD_MAP = CRM_EVENT_FIELD_MAP.copy()

EVENT_TYPE_ALIASES = {
    "open":        "opened",
    "email_open":  "opened",
    "email_opened":"opened",
    "click":       "clicked",
    "link_click":  "clicked",
    "reply":       "replied",
    "response":    "replied",
    "conversion":  "converted",
    "convert":     "converted",
    "email_sent":  "sent",
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except Exception:
        return default


def _normalize_event_type(event_type: str) -> str:
    cleaned = (event_type or "").strip().lower()
    return EVENT_TYPE_ALIASES.get(cleaned, cleaned)


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _within_last_days(timestamp_value: Any, last_days: Optional[int]) -> bool:
    if not last_days or last_days <= 0:
        return True
    ts = _parse_iso_datetime(timestamp_value)
    if ts is None:
        return False
    return ts >= (_utc_now() - timedelta(days=last_days))


def _clean_url(url: str) -> str:
    return (url or "").strip().split("?")[0].split("#")[0].rstrip("/").lower()


def _is_email_event(metadata: Dict[str, Any], event_type: str) -> bool:
    channel = str(metadata.get("channel") or "email").strip().lower()
    return (channel in {"email", "gmail"}) and (_normalize_event_type(event_type) in {
        "sent", "opened", "clicked", "replied", "converted",
    })


def _build_event_key(
    lead_id: Any,
    campaign_id: Optional[int],
    event_type: str,
    metadata: Dict[str, Any],
) -> str:
    normalized       = _normalize_event_type(event_type)
    gmail_message_id = str(metadata.get("gmail_message_id") or "").strip()
    thread_id        = str(metadata.get("thread_id") or "").strip()
    url              = _clean_url(str(metadata.get("url") or ""))
    sender           = str(metadata.get("sender") or metadata.get("from") or "").strip().lower()
    subject          = str(metadata.get("subject") or "").strip().lower()
    step             = str(metadata.get("followup_step") or metadata.get("step") or "").strip()
    click_date       = str(metadata.get("click_date") or "").strip()

    if normalized == "replied":
        anchor = thread_id or gmail_message_id or f"{sender}:{subject}"
    elif normalized == "clicked":
        anchor = click_date or url or gmail_message_id or f"{sender}:{url}"
    elif normalized == "opened":
        # Minute-precision timestamp — every open in a new minute gets
        # its own unique key so it is always inserted and counted.
        now_minute = _utc_now().strftime("%Y-%m-%dT%H:%M")
        anchor     = now_minute
    elif normalized == "sent":
        anchor = step or gmail_message_id or thread_id or f"{sender}:{subject}"
    else:
        anchor = gmail_message_id or thread_id or url or f"{sender}:{subject}"

    raw = f"{lead_id}|{campaign_id}|{normalized}|{anchor}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_exists(
    event_key: str,
    lead_id: Any,
    event_type: str,
    gmail_message_id: Optional[str],
    campaign_id: Optional[int] = None,
) -> bool:
    # ── FIX ───────────────────────────────────────────────────────────────
    # Opens are never deduplicated here AND _update_outreach_lead() does
    # not touch open counters. event_router skips log_event() for opens
    # entirely. pixel_server._track_open_db() is the sole pipeline for
    # open tracking. This function returning False for opens is kept as
    # a safety net in case log_event() is ever called for an open from
    # an unexpected code path — it ensures the insert still happens but
    # the counter update below is a no-op.
    # ──────────────────────────────────────────────────────────────────────
    if event_type == "opened":
        return False

    try:
        res  = (
            supabase.table("lead_events")
            .select("id, metadata, event_type")
            .eq("lead_id", lead_id)
            .eq("event_type", event_type)
            .order("timestamp", desc=True)
            .limit(100)
            .execute()
        )
        rows = res.data or []

        for row in rows:
            row_meta  = row.get("metadata") or {}
            if not isinstance(row_meta, dict):
                continue
            row_key   = str(row_meta.get("event_key") or "").strip()
            row_gmail = str(row_meta.get("gmail_message_id") or "").strip()
            row_cid   = row_meta.get("campaign_id")

            if campaign_id is not None and row_cid is not None:
                try:
                    if int(row_cid) != int(campaign_id):
                        continue
                except Exception:
                    pass

            if gmail_message_id and row_gmail and row_gmail == gmail_message_id:
                return True
            if row_key and row_key == event_key:
                return True

        return False
    except Exception:
        return False


def _update_outreach_lead(
    lead_id: Any,
    event_type: str,
    timestamp_iso: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Update outreach_leads for the given event.

    OPENED — updates flags and timestamps ONLY.
             Does NOT increment open_count or followup_open_count.
             Counter increments for opens are owned exclusively by
             pixel_server._track_open_db(). This function was
             previously the THIRD place incrementing open counters
             (after pixel_server and event_router), using a different
             routing rule (followup_status) that disagreed with the
             other two, causing both columns to go up on every open.
    """
    try:
        existing = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if not existing.data:
            return {"updated": False, "reason": "lead_not_found"}

        row    = existing.data[0]
        status = (row.get("status") or "").lower().strip()
        optional = {
            k: metadata.get(k)
            for k in ("thread_id", "gmail_message_id")
            if metadata.get(k) not in (None, "")
        }

        # ── sent ─────────────────────────────────────────────────────────
        if event_type == "sent":
            payload = {
                "status":          "sent",
                "last_email_sent": timestamp_iso,
                "last_contacted":  timestamp_iso,
                "last_updated":    timestamp_iso,
            }
            if optional:
                payload.update(optional)
            supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
            return {"updated": True, "payload": payload}

        # ── opened ───────────────────────────────────────────────────────
        if event_type == "opened":
            # ── FIX ──────────────────────────────────────────────────────
            # NO counter increments here — not open_count, not
            # followup_open_count, not email_opened_at on first open.
            #
            # pixel_server._track_open_db() writes all of these atomically
            # after its own fresh SELECT. Any write here risks:
            #   1. A race condition (stale read → wrong increment value)
            #   2. A double-count (counter written twice for one open)
            #   3. A cross-column count (wrong counter gets incremented)
            #
            # This block intentionally does nothing and returns immediately.
            # ─────────────────────────────────────────────────────────────
            print(
                f"⏭️ event_repo: open update skipped → lead_id={lead_id} "
                f"(pixel_server._track_open_db is the sole owner)"
            )
            return {
                "updated": False,
                "ignored": True,
                "note":    "open counters owned by pixel_server — not touched here",
            }

        # ── clicked ──────────────────────────────────────────────────────
        if event_type == "clicked":
            payload = {
                "click_count":  _as_int(row.get("click_count")) + 1,
                "link_clicked": True,
                "last_updated": timestamp_iso,
            }
            if status in {"pending", "new", "not_contacted"}:
                payload["status"] = "sent"
            supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
            return {"updated": True, "payload": payload}

        # ── replied ──────────────────────────────────────────────────────
        if event_type == "replied":
            base_payload = {
                "reply_count":      _as_int(row.get("reply_count")) + 1,
                "status":           "replied",
                "reply_status":     True,
                "followup_status":  "completed",
                "replied_at":       timestamp_iso,
                "last_contacted":   timestamp_iso,
                "last_updated":     timestamp_iso,
                "next_followup":    None,
                "thread_id":        metadata.get("thread_id"),
                "gmail_message_id": metadata.get("gmail_message_id"),
            }
            last_error = None
            for variant in [
                {**base_payload, **optional},
                base_payload,
            ]:
                try:
                    supabase.table("outreach_leads").update(variant).eq("id", lead_id).execute()
                    return {"updated": True, "payload": variant}
                except Exception as e:
                    last_error = str(e)
            return {"updated": False, "error": last_error}

        # ── converted ────────────────────────────────────────────────────
        if event_type == "converted":
            payload = {
                "conversion_count": _as_int(row.get("conversion_count")) + 1,
                "status":           "converted",
                "deal_closed":      True,
                "deal_status":      "won",
                "last_updated":     timestamp_iso,
            }
            supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
            return {"updated": True, "payload": payload}

        # ── failed ───────────────────────────────────────────────────────
        if event_type == "failed":
            payload = {
                "status":          "failed",
                "followup_status": "failed",
                "reply_status":    False,
                "last_updated":    timestamp_iso,
            }
            supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
            return {"updated": True, "payload": payload}

        return {"updated": False, "ignored": True}

    except Exception as e:
        return {"updated": False, "error": str(e)}


def _update_crm_analytics(
    lead_id: Any,
    event_type: str,
    timestamp_iso: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    if not _is_email_event(metadata, event_type):
        return {"updated": False, "ignored": True}

    field = CRM_EVENT_FIELD_MAP.get(event_type)
    if not field:
        return {"updated": False, "ignored": True}

    try:
        existing = (
            supabase.table("crm_analytics")
            .select("*")
            .eq("lead_id", lead_id)
            .limit(1)
            .execute()
        )

        if existing.data:
            row     = existing.data[0]
            payload = {
                "lead_id":     lead_id,
                "emails_sent": _as_int(row.get("emails_sent")),
                "opens":       _as_int(row.get("opens")),
                "clicks":      _as_int(row.get("clicks")),
                "replies":     _as_int(row.get("replies")),
                "conversions": _as_int(row.get("conversions")),
                "last_activity": timestamp_iso,
            }
        else:
            payload = {
                "lead_id":     lead_id,
                "emails_sent": 0,
                "opens":       0,
                "clicks":      0,
                "replies":     0,
                "conversions": 0,
                "last_activity": timestamp_iso,
            }

        payload[field] = _as_int(payload.get(field, 0)) + 1
        payload["engagement_score"] = (
            _as_int(payload["emails_sent"]) * 1
            + _as_int(payload["opens"])     * 2
            + _as_int(payload["clicks"])    * 3
            + _as_int(payload["replies"])   * 5
            + _as_int(payload["conversions"]) * 10
        )

        if existing.data:
            supabase.table("crm_analytics").update(payload).eq("lead_id", lead_id).execute()
        else:
            supabase.table("crm_analytics").insert(payload).execute()

        return {"updated": True, "field": field}

    except Exception as e:
        return {"updated": False, "error": str(e)}


def _update_campaign_analytics(
    campaign_id: Any,
    event_type: str,
    timestamp_iso: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    if not _is_email_event(metadata, event_type):
        return {"updated": False, "ignored": True}

    field = CAMPAIGN_EVENT_FIELD_MAP.get(event_type)
    if not field:
        return {"updated": False, "ignored": True}

    try:
        today    = _utc_now().date().isoformat()
        tomorrow = (_utc_now().date() + timedelta(days=1)).isoformat()

        existing = (
            supabase.table("campaign_analytics")
            .select("*")
            .eq("campaign_id", campaign_id)
            .gte("created_at", today)
            .lt("created_at", tomorrow)
            .limit(1)
            .execute()
        )

        if existing.data:
            row     = existing.data[0]
            payload = {
                "campaign_id":         campaign_id,
                "emails_sent":         _as_int(row.get("emails_sent")),
                "opens":               _as_int(row.get("opens")),
                "clicks":              _as_int(row.get("clicks")),
                "replies":             _as_int(row.get("replies")),
                "conversions":         _as_int(row.get("conversions")),
                "emails_per_provider": row.get("emails_per_provider") or {},
                "created_at":          today,
            }
            payload[field] = _as_int(payload.get(field, 0)) + 1
            supabase.table("campaign_analytics").update(payload).eq("id", row["id"]).execute()
        else:
            payload = {
                "campaign_id":         campaign_id,
                "emails_sent":         0,
                "opens":               0,
                "clicks":              0,
                "replies":             0,
                "conversions":         0,
                "emails_per_provider": {},
                "created_at":          today,
            }
            payload[field] = 1
            supabase.table("campaign_analytics").insert(payload).execute()

        return {"updated": True, "field": field}

    except Exception as e:
        return {"updated": False, "error": str(e)}


def _insert_lead_event(payload: Dict[str, Any]) -> Any:
    try:
        return supabase.table("lead_events").insert(payload).execute()
    except Exception as e:
        msg = str(e).lower()
        if "campaign_id" in msg or "column" in msg or "schema cache" in msg or "does not exist" in msg:
            fallback = dict(payload)
            fallback.pop("campaign_id", None)
            return supabase.table("lead_events").insert(fallback).execute()
        raise


def log_event(
    lead_id: Any,
    campaign_id: Optional[int],
    event_type: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = _normalize_event_type(event_type)
    safe_meta  = _json_safe(metadata or {})
    if not isinstance(safe_meta, dict):
        safe_meta = {}

    safe_meta.setdefault("channel", "email")

    gmail_message_id = str(safe_meta.get("gmail_message_id") or "").strip()
    event_key        = str(safe_meta.get("event_key") or "").strip() or _build_event_key(
        lead_id=lead_id, campaign_id=campaign_id,
        event_type=normalized, metadata=safe_meta,
    )
    safe_meta["event_key"] = event_key
    if campaign_id is not None:
        safe_meta["campaign_id"] = campaign_id
    if gmail_message_id:
        safe_meta["gmail_message_id"] = gmail_message_id

    # _event_exists returns False for opens unconditionally so opens
    # always get a new event row. For all other types dedup applies.
    if _event_exists(
        event_key=event_key, lead_id=lead_id, event_type=normalized,
        gmail_message_id=gmail_message_id or None, campaign_id=campaign_id,
    ):
        return {"status": "duplicate", "event_key": event_key}

    timestamp_iso = _utc_now_iso()
    payload: Dict[str, Any] = {
        "lead_id":    lead_id,
        "event_type": normalized,
        "timestamp":  timestamp_iso,
        "metadata":   safe_meta,
    }
    if campaign_id is not None:
        payload["campaign_id"] = campaign_id

    try:
        res             = _insert_lead_event(payload)
        # _update_outreach_lead does nothing for opens (returns immediately)
        outreach_result = _update_outreach_lead(lead_id, normalized, timestamp_iso, safe_meta)
        crm_result      = _update_crm_analytics(lead_id, normalized, timestamp_iso, safe_meta)
        campaign_result = (
            _update_campaign_analytics(campaign_id, normalized, timestamp_iso, safe_meta)
            if campaign_id is not None else None
        )

        print(f"✅ Event logged: {normalized} | lead={lead_id}")
        return {
            "status":             "success",
            "event_key":          event_key,
            "data":               getattr(res, "data", None),
            "outreach_leads":     outreach_result,
            "crm_analytics":      crm_result,
            "campaign_analytics": campaign_result,
        }

    except Exception as e:
        print(f"❌ Event logging failed: {e}")
        return {"status": "error", "event_key": event_key, "message": str(e)}


def store_event(
    lead_id: Any,
    event_type: str,
    campaign_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return log_event(
        lead_id=lead_id, campaign_id=campaign_id,
        event_type=event_type, metadata=metadata,
    )


def get_events_by_lead(lead_id: Any, last_days: Optional[int] = None) -> List[Dict[str, Any]]:
    try:
        res  = (
            supabase.table("lead_events")
            .select("*")
            .eq("lead_id", lead_id)
            .order("timestamp", desc=True)
            .execute()
        )
        rows = res.data or []
        if last_days and last_days > 0:
            rows = [r for r in rows if _within_last_days(
                r.get("timestamp") or r.get("created_at"), last_days
            )]
        return rows
    except Exception:
        return []


get_lead_events     = get_events_by_lead
get_events_for_lead = get_events_by_lead


def get_events_by_campaign(
    campaign_id: int, last_days: Optional[int] = None
) -> List[Dict[str, Any]]:
    try:
        res  = (
            supabase.table("lead_events")
            .select("*")
            .eq("campaign_id", campaign_id)
            .order("timestamp", desc=True)
            .execute()
        )
        rows = res.data or []
        if last_days and last_days > 0:
            rows = [r for r in rows if _within_last_days(
                r.get("timestamp") or r.get("created_at"), last_days
            )]
        return rows
    except Exception:
        try:
            res  = supabase.table("lead_events").select("*").order("timestamp", desc=True).execute()
            rows = res.data or []
            out  = []
            for r in rows:
                md = r.get("metadata") or {}
                if not isinstance(md, dict):
                    continue
                try:
                    if int(md.get("campaign_id", -1)) != int(campaign_id):
                        continue
                except Exception:
                    continue
                if last_days and last_days > 0 and not _within_last_days(
                    r.get("timestamp") or r.get("created_at"), last_days
                ):
                    continue
                out.append(r)
            return out
        except Exception:
            return []


get_campaign_events = get_events_by_campaign


def count_events(
    campaign_id: int,
    event_type: Optional[str] = None,
    last_days: Optional[int] = None,
) -> int:
    events = get_events_by_campaign(campaign_id, last_days=last_days)
    if event_type:
        normalized = _normalize_event_type(event_type)
        return sum(
            1 for e in events
            if _normalize_event_type(e.get("event_type")) == normalized
        )
    return len(events)


def get_campaign_metrics(
    campaign_id: int, last_days: Optional[int] = None
) -> Dict[str, Any]:
    events      = get_events_by_campaign(campaign_id, last_days=last_days)
    emails_sent = opens = clicks = replies = conversions = 0

    for e in events:
        md = e.get("metadata") or {}
        et = _normalize_event_type(e.get("event_type"))
        if not _is_email_event(md, et):
            continue
        if et == "sent":        emails_sent += 1
        elif et == "opened":    opens       += 1
        elif et == "clicked":   clicks      += 1
        elif et == "replied":   replies     += 1
        elif et == "converted": conversions += 1

    return {
        "emails_sent":     emails_sent,
        "opens":           opens,
        "clicks":          clicks,
        "replies":         replies,
        "conversions":     conversions,
        "open_rate":       round((opens / emails_sent) * 100, 1) if emails_sent else 0,
        "click_rate":      round((clicks / emails_sent) * 100, 1) if emails_sent else 0,
        "reply_rate":      round((replies / emails_sent) * 100, 1) if emails_sent else 0,
        "conversion_rate": round((conversions / emails_sent) * 100, 1) if emails_sent else 0,
    }


def get_campaign_funnel(
    campaign_id: int, last_days: Optional[int] = None
) -> Dict[str, Any]:
    events     = get_events_by_campaign(campaign_id, last_days=last_days)
    sent = opened = clicked = replied = converted = 0

    for e in events:
        md = e.get("metadata") or {}
        et = _normalize_event_type(e.get("event_type"))
        if not _is_email_event(md, et):
            continue
        if et == "sent":        sent      += 1
        elif et == "opened":    opened    += 1
        elif et == "clicked":   clicked   += 1
        elif et == "replied":   replied   += 1
        elif et == "converted": converted += 1

    return {
        "sent":      sent,
        "opened":    opened,
        "clicked":   clicked,
        "replied":   replied,
        "converted": converted,
        "drop_off_to_reply_pct":      round((sent - replied) / sent * 100, 1) if sent else 0,
        "drop_off_to_conversion_pct": round((replied - converted) / replied * 100, 1) if replied else 0,
    }


def log_email_sent(lead_id: Any, campaign_id: int) -> Dict[str, Any]:
    return log_event(lead_id, campaign_id, "sent",    {"channel": "email"})

def log_email_opened(lead_id: Any, campaign_id: int) -> Dict[str, Any]:
    return log_event(lead_id, campaign_id, "opened",  {"channel": "email"})

def log_link_clicked(lead_id: Any, campaign_id: int) -> Dict[str, Any]:
    return log_event(lead_id, campaign_id, "clicked", {"channel": "email"})

def log_reply(lead_id: Any, campaign_id: int) -> Dict[str, Any]:
    return log_event(lead_id, campaign_id, "replied", {"channel": "email"})

def log_conversion(lead_id: Any, campaign_id: int, revenue: float) -> Dict[str, Any]:
    return log_event(lead_id, campaign_id, "converted", {"revenue": revenue, "channel": "email"})

def log_ai_action(
    lead: Dict[str, Any],
    action: str,
    priority_score: float,
    reply_probability: float,
    predicted_revenue: float,
) -> Dict[str, Any]:
    return log_event(
        lead_id=lead["id"], campaign_id=lead.get("campaign_id"),
        event_type="ai_action",
        metadata={
            "action":            action,
            "priority_score":    priority_score,
            "reply_probability": reply_probability,
            "predicted_revenue": predicted_revenue,
            "channel":           "email",
        },
    )

def delete_old_events(days: int = 90) -> Dict[str, Any]:
    try:
        cutoff = (_utc_now() - timedelta(days=days)).isoformat()
        res    = supabase.table("lead_events").delete().lt("timestamp", cutoff).execute()
        return {"deleted": True, "data": res.data}
    except Exception as e:
        return {"deleted": False, "error": str(e)}
