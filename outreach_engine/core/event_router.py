# outreach_engine/core/event_router.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import log_event

ANALYTICS_ONLY = {"clicked"}

_EVENT_ALIASES = {
    "open":        "opened",
    "opened":      "opened",
    "click":       "clicked",
    "clicked":     "clicked",
    "reply":       "replied",
    "replied":     "replied",
    "sent":        "sent",
    "converted":   "converted",
    "conversion":  "converted",
    "failed":      "failed",
    "optout":      "opt_out",
    "opt_out":     "opt_out",
    "unsubscribe": "opt_out",
    "unsubscribed":"opt_out",
}

_REPLY_TERMINAL_STATUSES = {"replied", "converted", "won", "lost", "closed"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_event_type(event_type: str) -> str:
    key = (event_type or "").strip().lower()
    return _EVENT_ALIASES.get(key, key)


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _fetch_outreach_lead(lead_id: Any) -> Optional[Dict[str, Any]]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    except Exception:
        pass
    return None


def _update_outreach_lead(lead_id: Any, payload: Dict[str, Any]) -> None:
    try:
        supabase.table("outreach_leads") \
            .update(payload) \
            .eq("id", lead_id) \
            .execute()
    except Exception:
        pass


def handle_event(
    event_type: str,
    campaign_id: int,
    lead_id: Optional[Any] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Central event router.

    OPENS:
      Every open counter write is owned exclusively by
      pixel_server._track_open_db(). This function must NOT call
      log_event() for opens because log_event() calls
      _update_outreach_lead(), _update_crm_analytics(), and
      _update_campaign_analytics() — all of which would increment
      open counters a second time.

      pixel_server performs these writes atomically:
        • outreach_leads.open_count / followup_open_count
        • outreach_leads.email_opened / email_opened_at
        • leads.open_count / email_opened / email_opened_at
        • crm_analytics.opens
        • campaign_analytics.opens
        • lead_events (open row)

      Nothing else may touch any of these for opens.

    CLICKS:
      Analytics only — increments click_count via direct DB update.
      log_event() is called for event logging.

    REPLIES:
      Logs the event. Applies status fields only if lead is not
      already in a terminal reply state. reply_count is owned
      exclusively by gmail_watcher — NOT incremented here.

    SENT / CONVERTED / FAILED / OPT_OUT:
      Apply the appropriate state transition.
    """
    if lead_id is None:
        return {"status": "error", "message": "lead_id is required"}
    if campaign_id is None:
        return {"status": "error", "message": "campaign_id is required"}

    normalized = _normalize_event_type(event_type)
    metadata   = _safe_dict(metadata)

    # ── Opened — complete no-op ───────────────────────────────────────────
    if normalized == "opened":
        # Do NOT call log_event() here.
        # log_event() → _update_outreach_lead() sets email_opened=True
        # log_event() → _update_crm_analytics() increments opens
        # log_event() → _update_campaign_analytics() increments opens
        # All three are already done by pixel_server._track_open_db().
        # Calling log_event() here would double-count every open.
        print(
            f"⏭️ event_router: open event skipped entirely → "
            f"lead_id={lead_id} "
            f"(pixel_server._track_open_db owns all open writes)"
        )
        return {
            "status":     "success",
            "event_type": normalized,
            "route": {
                "routed_to": "none",
                "action": (
                    "skipped — pixel_server owns all open counter "
                    "writes including lead_events, crm_analytics, "
                    "campaign_analytics, outreach_leads, leads"
                ),
            },
        }

    # ── For all other event types: log the event ──────────────────────────
    try:
        log_result = log_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type=normalized,
            metadata=metadata,
        )
    except Exception as e:
        return {
            "status": "error",
            "stage": "log_event",
            "message": str(e),
        }

    # ── Click: analytics only ─────────────────────────────────────────────
    if normalized in ANALYTICS_ONLY:
        lead = _fetch_outreach_lead(lead_id)
        if lead:
            _update_outreach_lead(lead_id, {
                "click_count":  int(lead.get("click_count") or 0) + 1,
                "link_clicked": True,
                "last_updated": _now_iso(),
            })
        return {
            "status":     "success",
            "event_type": normalized,
            "route": {
                "routed_to": "analytics_only",
                "action":    "click_count_incremented",
            },
            "log": log_result,
        }

    lead = _fetch_outreach_lead(lead_id)

    if not lead:
        return {
            "status":     "success",
            "event_type": normalized,
            "route": {"routed_to": "none", "action": "lead_not_found"},
            "log":   log_result,
        }

    current_status = (lead.get("status") or "").strip().lower()

    # ── Sent ──────────────────────────────────────────────────────────────
    if normalized == "sent":
        _update_outreach_lead(lead_id, {
            "status":          "sent",
            "last_email_sent": metadata.get("sent_at") or _now_iso(),
            "last_contacted":  metadata.get("sent_at") or _now_iso(),
            "last_updated":    _now_iso(),
        })
        return {
            "status":     "success",
            "event_type": normalized,
            "route": {"routed_to": "lead_state", "action": "mark_sent"},
            "log":   log_result,
        }

    # ── Replied ───────────────────────────────────────────────────────────
    if normalized == "replied":
        if current_status in _REPLY_TERMINAL_STATUSES:
            print(
                f"📩 event_router: reply received but lead {lead_id} "
                f"already in terminal status={current_status} — "
                f"log only, no state change"
            )
            return {
                "status":     "success",
                "event_type": normalized,
                "route": {
                    "routed_to": "log_only",
                    "action":    "already_terminal_no_state_change",
                },
                "log": log_result,
            }

        # Status fields only — reply_count owned by gmail_watcher
        _update_outreach_lead(lead_id, {
            "status":          "replied",
            "followup_status": "completed",
            "next_followup":   None,
            "replied_at":      metadata.get("timestamp") or _now_iso(),
            "last_contacted":  metadata.get("timestamp") or _now_iso(),
            "last_updated":    _now_iso(),
            # reply_count NOT touched — gmail_watcher owns it
        })
        print(
            f"📩 event_router: reply status applied → lead_id={lead_id} "
            f"(reply_count managed by gmail_watcher)"
        )
        return {
            "status":     "success",
            "event_type": normalized,
            "route": {
                "routed_to": "lead_state",
                "action":    "mark_replied_status_only",
            },
            "log": log_result,
        }

    # ── Terminal: converted / failed / opt_out / completed ────────────────
    if normalized in {"converted", "failed", "opt_out", "completed"}:
        term_payload: Dict[str, Any] = {
            "status":       normalized,
            "last_updated": _now_iso(),
        }
        if normalized == "converted":
            term_payload["conversion_count"] = (
                int(lead.get("conversion_count") or 0) + 1
            )
        if normalized in {"failed", "opt_out"}:
            term_payload["followup_status"] = normalized
            term_payload["next_followup"]   = None

        _update_outreach_lead(lead_id, term_payload)
        return {
            "status":     "success",
            "event_type": normalized,
            "route": {
                "routed_to": "lead_state",
                "action":    f"mark_{normalized}",
            },
            "log": log_result,
        }

    # ── Fallback ──────────────────────────────────────────────────────────
    return {
        "status":     "success",
        "event_type": normalized,
        "route": {"routed_to": "unhandled", "action": "none"},
        "log":   log_result,
    }
