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
    "open":         "opened",
    "email_open":   "opened",
    "email_opened": "opened",
    "click":        "clicked",
    "link_click":   "clicked",
    "reply":        "replied",
    "response":     "replied",
    "conversion":   "converted",
    "convert":      "converted",
    "email_sent":   "sent",
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


def _normalize_event_type(event_type: str) -> str:
    cleaned = (event_type or "").strip().lower()
    return EVENT_TYPE_ALIASES.get(cleaned, cleaned)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _within_last_days(timestamp_value: Any, last_days: Optional[int]) -> bool:
    if not last_days or last_days <= 0:
        return True
    ts = _parse_iso_datetime(timestamp_value)
    if ts is None:
        return False
    cutoff = _utc_now() - timedelta(days=last_days)
    return ts >= cutoff


def _get_channel(metadata: Dict[str, Any]) -> str:
    return str(metadata.get("channel") or "email").strip().lower()


def _is_email_event(metadata: Dict[str, Any], event_type: str) -> bool:
    channel = _get_channel(metadata)
    return channel in {"email", "gmail"} and _normalize_event_type(event_type) in {
        "sent",
        "opened",
        "clicked",
        "replied",
        "converted",
    }


def _clean_url(url: str) -> str:
    return (url or "").strip().split("?")[0].split("#")[0].rstrip("/").lower()


def _build_event_key(
    lead_id: Any,
    campaign_id: Optional[int],
    event_type: str,
    metadata: Dict[str, Any],
) -> str:
    normalized = _normalize_event_type(event_type)

    gmail_message_id = str(metadata.get("gmail_message_id") or "").strip()
    thread_id        = str(
        metadata.get("thread_id") or metadata.get("thread") or ""
    ).strip()
    url = _clean_url(
        str(
            metadata.get("url")
            or metadata.get("destination_url")
            or metadata.get("redirect")
            or ""
        )
    )
    sender     = str(
        metadata.get("sender") or metadata.get("from") or ""
    ).strip().lower()
    subject    = str(metadata.get("subject") or "").strip().lower()
    step       = str(
        metadata.get("followup_step") or metadata.get("step") or ""
    ).strip()
    click_date = str(metadata.get("click_date") or "").strip()

    if normalized == "replied":
        anchor = thread_id or gmail_message_id or f"{sender}:{subject}"

    elif normalized == "clicked":
        anchor = (
            click_date
            or url
            or gmail_message_id
            or thread_id
            or f"{sender}:{subject}"
        )

    elif normalized == "opened":
        # Minute-precision timestamp so every open in a new minute gets
        # its own unique key and is always inserted / counted.
        now_minute = _utc_now().strftime("%Y-%m-%dT%H:%M")
        anchor     = now_minute

    elif normalized == "sent":
        anchor = (
            step or gmail_message_id or thread_id or f"{sender}:{subject}"
        )

    else:
        anchor = (
            gmail_message_id or thread_id or url or f"{sender}:{subject}"
        )

    raw = f"{lead_id}|{campaign_id}|{normalized}|{anchor}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_exists(
    event_key: str,
    lead_id: Any,
    event_type: str,
    gmail_message_id: Optional[str],
    campaign_id: Optional[int] = None,
) -> bool:
    # Opens are never deduplicated here.
    # Dedup for opens lives exclusively in pixel_server (2s burst window).
    if event_type == "opened":
        return False

    try:
        res = (
            supabase.table("lead_events")
            .select("id, metadata, event_type, timestamp")
            .eq("lead_id", lead_id)
            .eq("event_type", event_type)
            .order("timestamp", desc=True)
            .limit(100)
            .execute()
        )
        rows = res.data or []

        for row in rows:
            row_meta = row.get("metadata") or {}
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

    OPENED — only updates boolean flags and timestamps.
             Does NOT increment open_count or followup_open_count.
             Does NOT promote status for leads already in "sent" state
             (only promotes from pending/new/not_contacted to avoid
             incorrectly re-stamping a lead that opened a follow-up).

    Counter ownership:
      open_count / followup_open_count → pixel_server._track_open_db()
      click_count                      → this function (clicked branch)
      reply_count                      → gmail_watcher exclusively
      conversion_count                 → this function (converted branch)
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
            return {
                "updated": False,
                "ignored": True,
                "reason":  "lead_not_found",
            }

        row              = existing.data[0]
        status           = (row.get("status") or "").lower().strip()
        click_count      = _as_int(row.get("click_count",      0))
        reply_count      = _as_int(row.get("reply_count",      0))
        conversion_count = _as_int(row.get("conversion_count", 0))

        optional_fields = {
            key: metadata.get(key)
            for key in ("thread_id", "gmail_message_id")
            if metadata.get(key) not in (None, "")
        }

        # ── sent ─────────────────────────────────────────────────────────
        if event_type == "sent":
            payload: Dict[str, Any] = {
                "status":          "sent",
                "last_email_sent": timestamp_iso,
                "last_updated":    timestamp_iso,
            }
            if optional_fields:
                payload.update(optional_fields)
            supabase.table("outreach_leads").update(payload).eq(
                "id", lead_id
            ).execute()
            return {"updated": True, "payload": payload}

        # ── opened ───────────────────────────────────────────────────────
        if event_type == "opened":
            # ── FIX ──────────────────────────────────────────────────────
            # NO counter increments here.
            # pixel_server._track_open_db() is the sole owner of:
            #   • outreach_leads.open_count
            #   • outreach_leads.followup_open_count
            #   • outreach_leads.email_opened_at (first-open stamp)
            #   • leads.open_count
            #   • crm_analytics.opens
            #   • lead_events (open row)
            #
            # Previously this function incremented open_count or
            # followup_open_count via followup_status routing while
            # pixel_server used sent_email_type routing. The two rules
            # disagreed and hit DIFFERENT columns — both open_count AND
            # followup_open_count went up by 1 on every single open.
            # ─────────────────────────────────────────────────────────────
            payload = {
                "email_opened": True,
                "last_updated": timestamp_iso,
            }

            # Set email_opened_at only on the very first open
            if not row.get("email_opened"):
                payload["email_opened_at"] = timestamp_iso

            # Promote out of un-contacted states only — do NOT overwrite
            # "sent", "followup_no_open", etc. with "sent" again.
            if status in {"pending", "new", "not_contacted"}:
                payload["status"] = "sent"

            supabase.table("outreach_leads").update(payload).eq(
                "id", lead_id
            ).execute()
            return {
                "updated": True,
                "payload": payload,
                "note":    "counters owned by pixel_server — not touched here",
            }

        # ── clicked ──────────────────────────────────────────────────────
        if event_type == "clicked":
            payload = {
                "click_count":  click_count + 1,
                "link_clicked": True,
                "last_updated": timestamp_iso,
            }
            if status in {"pending", "new", "not_contacted"}:
                payload["status"] = "sent"
            supabase.table("outreach_leads").update(payload).eq(
                "id", lead_id
            ).execute()
            return {"updated": True, "payload": payload}

        # ── replied ──────────────────────────────────────────────────────
        if event_type == "replied":
            # reply_count is incremented here because this path is the
            # event_repository path (not the gmail_watcher path).
            # gmail_watcher calls _increment_reply_count_and_finalize()
            # directly and does NOT call log_event() / _update_outreach_lead().
            # These two paths are mutually exclusive — no double-count.
            base_payload: Dict[str, Any] = {
                "reply_count":      reply_count + 1,
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
                {**base_payload, **optional_fields},
                base_payload,
            ]:
                try:
                    supabase.table("outreach_leads").update(variant).eq(
                        "id", lead_id
                    ).execute()
                    return {"updated": True, "payload": variant}
                except Exception as e:
                    last_error = str(e)

            return {"updated": False, "error": last_error}

        # ── converted ────────────────────────────────────────────────────
        if event_type == "converted":
            payload = {
                "conversion_count": conversion_count + 1,
                "status":           "converted",
                "last_updated":     timestamp_iso,
            }
            supabase.table("outreach_leads").update(payload).eq(
                "id", lead_id
            ).execute()
            return {"updated": True, "payload": payload}

        # ── failed ───────────────────────────────────────────────────────
        if event_type == "failed":
            payload = {
                "status":       "failed",
                "last_updated": timestamp_iso,
            }
            supabase.table("outreach_leads").update(payload).eq(
                "id", lead_id
            ).execute()
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
    """
    OPENS — absolute no-op.

    pixel_server._track_open_db() → pixel_server._update_crm_analytics()
    already incremented crm_analytics.opens when the pixel fired.

    If this function also increments opens, every open produces a
    count of 2 in crm_analytics regardless of which branch incremented
    outreach_leads (they could even be different columns).

    This function is reached from log_event() which is called from:
      • event_router.handle_event() — already gates opens BEFORE calling
        log_event(), so opens never arrive here via that path.
      • log_email_opened() convenience wrapper — fixed below to be a
        permanent no-op so it never calls log_event() for opens.
      • Any direct caller of log_event() with event_type="opened".

    The gate here is defense-in-depth: even if log_event() is somehow
    called for an open, crm_analytics will never be double-incremented.

    All other event types (sent, clicked, replied, converted) are
    handled normally.
    """
    if event_type == "opened":
        print(
            f"⏭️ event_repository._update_crm_analytics: "
            f"open skipped → lead_id={lead_id} "
            f"(pixel_server._update_crm_analytics is sole owner of opens)"
        )
        return {
            "updated": False,
            "ignored": True,
            "reason":  "opens owned by pixel_server._update_crm_analytics",
        }

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
            row         = existing.data[0]
            emails_sent = _as_int(row.get("emails_sent"))
            opens       = _as_int(row.get("opens"))   # never touched for opens
            clicks      = _as_int(row.get("clicks"))
            replies     = _as_int(row.get("replies"))
            conversions = _as_int(row.get("conversions"))

            # "opened" is already blocked above — these branches only
            # run for sent / clicked / replied / converted.
            if field == "emails_sent":   emails_sent += 1
            elif field == "clicks":      clicks      += 1
            elif field == "replies":     replies     += 1
            elif field == "conversions": conversions += 1
            # field == "opens" is unreachable here

            payload = {
                "last_activity":    timestamp_iso,
                "emails_sent":      emails_sent,
                "opens":            opens,
                "clicks":           clicks,
                "replies":          replies,
                "conversions":      conversions,
                "engagement_score": (
                    emails_sent * 1
                    + opens     * 2
                    + clicks    * 3
                    + replies   * 5
                    + conversions * 10
                ),
            }
            supabase.table("crm_analytics").update(payload).eq(
                "lead_id", lead_id
            ).execute()
            return {"updated": True, "mode": "update", "field": field}

        # No existing row — insert with zeroes + this event's field = 1.
        # opens is always 0 here because opened is blocked above.
        payload = {
            "lead_id":      lead_id,
            "emails_sent":  0,
            "opens":        0,   # pixel_server owns; never set to 1 here
            "clicks":       0,
            "replies":      0,
            "conversions":  0,
            "last_activity": timestamp_iso,
            "engagement_score": 0,
        }
        payload[field] = 1
        payload["engagement_score"] = (
            payload["emails_sent"] * 1
            + payload["opens"]     * 2
            + payload["clicks"]    * 3
            + payload["replies"]   * 5
            + payload["conversions"] * 10
        )
        supabase.table("crm_analytics").insert(payload).execute()
        return {"updated": True, "mode": "insert", "field": field}

    except Exception as e:
        return {"updated": False, "error": str(e), "field": field}


def _update_campaign_analytics(
    campaign_id: Any,
    event_type: str,
    timestamp_iso: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    OPENS — absolute no-op.

    pixel_server._track_open_db() owns the open write pipeline.
    campaign_analytics.opens must not be incremented here for the same
    reason as crm_analytics — it would be a second increment on top of
    what pixel_server already wrote (or will write via its own path).
    """
    if event_type == "opened":
        print(
            f"⏭️ event_repository._update_campaign_analytics: "
            f"open skipped → campaign_id={campaign_id} "
            f"(pixel_server is sole owner of open writes)"
        )
        return {
            "updated": False,
            "ignored": True,
            "reason":  "opens owned by pixel_server",
        }

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
            supabase.table("campaign_analytics").update(payload).eq(
                "id", row["id"]
            ).execute()
            return {"updated": True, "mode": "update", "field": field}

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
        return {"updated": True, "mode": "insert", "field": field}

    except Exception as e:
        return {"updated": False, "error": str(e), "field": field}


def _insert_lead_event(payload: Dict[str, Any]) -> Any:
    try:
        return supabase.table("lead_events").insert(payload).execute()
    except Exception as e:
        msg = str(e).lower()
        if (
            "campaign_id" in msg
            or "column" in msg
            or "schema cache" in msg
            or "does not exist" in msg
        ):
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
    """
    Central event logger.

    OPENS — log_event() must NOT be called for open events from any
    external caller. The correct open tracking path is exclusively:
      pixel_server._handle_open() → _track_open_db()

    If log_event() IS called for an open (e.g. from log_email_opened()
    or a direct caller), the three downstream functions that touch
    counters (_update_outreach_lead, _update_crm_analytics,
    _update_campaign_analytics) are all individually gated to be no-ops
    for opens. The lead_events INSERT still runs — this is intentional
    so that event history is complete — but no counter is touched.

    All other event types flow normally.
    """
    normalized = _normalize_event_type(event_type)
    safe_meta  = _json_safe(metadata or {})
    if not isinstance(safe_meta, dict):
        safe_meta = {}

    safe_meta.setdefault("channel", "email")

    gmail_message_id = str(safe_meta.get("gmail_message_id") or "").strip()

    event_key = str(safe_meta.get("event_key") or "").strip() or _build_event_key(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type=normalized,
        metadata=safe_meta,
    )
    safe_meta["event_key"] = event_key

    if campaign_id is not None:
        safe_meta["campaign_id"] = campaign_id
    if gmail_message_id:
        safe_meta["gmail_message_id"] = gmail_message_id

    # _event_exists() returns False unconditionally for "opened" so the
    # duplicate-guard is only active for non-open event types.
    if _event_exists(
        event_key=event_key,
        lead_id=lead_id,
        event_type=normalized,
        gmail_message_id=gmail_message_id or None,
        campaign_id=campaign_id,
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
        res = _insert_lead_event(payload)

        # All three functions below are individually gated for opens:
        # they will no-op and return immediately if normalized == "opened".
        outreach_result = _update_outreach_lead(
            lead_id=lead_id,
            event_type=normalized,
            timestamp_iso=timestamp_iso,
            metadata=safe_meta,
        )

        crm_result = _update_crm_analytics(
            lead_id=lead_id,
            event_type=normalized,
            timestamp_iso=timestamp_iso,
            metadata=safe_meta,
        )

        campaign_result = (
            _update_campaign_analytics(
                campaign_id=campaign_id,
                event_type=normalized,
                timestamp_iso=timestamp_iso,
                metadata=safe_meta,
            )
            if campaign_id is not None
            else None
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
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type=event_type,
        metadata=metadata,
    )


def get_lead_events(
    lead_id: Any, last_days: Optional[int] = None
) -> List[Dict[str, Any]]:
    try:
        res = (
            supabase.table("lead_events")
            .select("*")
            .eq("lead_id", lead_id)
            .order("timestamp", desc=True)
            .execute()
        )
        rows = res.data or []
        if last_days and last_days > 0:
            rows = [
                r for r in rows
                if _within_last_days(
                    r.get("timestamp") or r.get("created_at"), last_days
                )
            ]
        return rows
    except Exception:
        return []


get_events_by_lead  = get_lead_events
get_events_for_lead = get_lead_events


def get_campaign_events(
    campaign_id: int, last_days: Optional[int] = None
) -> List[Dict[str, Any]]:
    try:
        try:
            res = (
                supabase.table("lead_events")
                .select("*")
                .eq("campaign_id", campaign_id)
                .order("timestamp", desc=True)
                .execute()
            )
            rows = res.data or []
            if rows:
                if last_days and last_days > 0:
                    rows = [
                        r for r in rows
                        if _within_last_days(
                            r.get("timestamp") or r.get("created_at"),
                            last_days,
                        )
                    ]
                return rows
        except Exception:
            pass

        res  = (
            supabase.table("lead_events")
            .select("*")
            .order("timestamp", desc=True)
            .execute()
        )
        rows = res.data or []
        out: List[Dict[str, Any]] = []
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


get_events_by_campaign = get_campaign_events


def count_events(
    campaign_id: int,
    event_type: Optional[str] = None,
    last_days: Optional[int] = None,
) -> int:
    events = get_campaign_events(campaign_id, last_days=last_days)
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
    events      = get_campaign_events(campaign_id, last_days=last_days)
    emails_sent = opens = clicks = replies = conversions = 0

    for e in events:
        md = e.get("metadata") or {}
        if not isinstance(md, dict):
            md = {}
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
    events     = get_campaign_events(campaign_id, last_days=last_days)
    total_sent = opened = clicked = replied = converted = 0

    for e in events:
        md = e.get("metadata") or {}
        if not isinstance(md, dict):
            md = {}
        et = _normalize_event_type(e.get("event_type"))
        if not _is_email_event(md, et):
            continue
        if et == "sent":        total_sent += 1
        elif et == "opened":    opened     += 1
        elif et == "clicked":   clicked    += 1
        elif et == "replied":   replied    += 1
        elif et == "converted": converted  += 1

    return {
        "sent":       total_sent,
        "total_sent": total_sent,
        "opened":     opened,
        "clicked":    clicked,
        "replied":    replied,
        "converted":  converted,
        "drop_off_to_reply_pct": (
            round((total_sent - replied) / total_sent * 100, 1)
            if total_sent else 0
        ),
        "drop_off_to_conversion_pct": (
            round((replied - converted) / replied * 100, 1)
            if replied else 0
        ),
    }


def log_ai_action(
    lead: Dict[str, Any],
    action: str,
    priority_score: float,
    reply_probability: float,
    predicted_revenue: float,
) -> Dict[str, Any]:
    return log_event(
        lead_id=lead["id"],
        campaign_id=lead.get("campaign_id"),
        event_type="ai_action",
        metadata={
            "action":            action,
            "priority_score":    priority_score,
            "reply_probability": reply_probability,
            "predicted_revenue": predicted_revenue,
            "channel":           "email",
        },
    )


def log_rl_decision(lead: Dict[str, Any], action: str) -> Dict[str, Any]:
    return log_event(
        lead_id=lead["id"],
        campaign_id=lead.get("campaign_id"),
        event_type="rl_decision",
        metadata={"action": action, "channel": "email"},
    )


def log_conversion(
    lead_id: Any, campaign_id: int, revenue: float
) -> Dict[str, Any]:
    return log_event(
        lead_id, campaign_id, "converted",
        {"revenue": revenue, "channel": "email"},
    )


def log_email_sent(lead_id: Any, campaign_id: int) -> Dict[str, Any]:
    return log_event(lead_id, campaign_id, "sent", {"channel": "email"})


def log_email_opened(lead_id: Any, campaign_id: int) -> Dict[str, Any]:
    """
    FIX: Permanent no-op.

    Previously this called log_event() → _update_crm_analytics() which
    incremented crm_analytics.opens. That was a second increment on top
    of what pixel_server._track_open_db() already wrote, causing every
    open to be counted twice in crm_analytics (and potentially in
    campaign_analytics too).

    Open tracking is exclusively owned by:
      pixel_server._handle_open() → _track_open_db()

    This function is kept (not deleted) to avoid breaking any existing
    callers. It is now a safe, silent no-op that returns a skipped status.

    If you need to record that an email was opened, the open must flow
    through the pixel URL — not through this function.
    """
    print(
        f"⏭️ event_repository.log_email_opened: no-op "
        f"→ lead_id={lead_id} "
        f"(pixel_server._track_open_db owns all open tracking)"
    )
    return {
        "status":  "skipped",
        "reason":  "opens owned by pixel_server._track_open_db",
        "lead_id": lead_id,
    }


def log_link_clicked(lead_id: Any, campaign_id: int) -> Dict[str, Any]:
    return log_event(lead_id, campaign_id, "clicked", {"channel": "email"})


def log_reply(lead_id: Any, campaign_id: int) -> Dict[str, Any]:
    return log_event(lead_id, campaign_id, "replied", {"channel": "email"})


def delete_old_events(days: int = 90) -> Dict[str, Any]:
    try:
        cutoff = (_utc_now() - timedelta(days=days)).isoformat()
        res    = (
            supabase.table("lead_events")
            .delete()
            .lt("timestamp", cutoff)
            .execute()
        )
        return {"deleted": True, "data": res.data}
    except Exception as e:
        return {"deleted": False, "error": str(e)}
