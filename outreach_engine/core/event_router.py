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


def _now_naive_iso() -> str:
    return datetime.utcnow().isoformat()


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
      open_count and followup_open_count are owned exclusively by
      pixel_server._track_open_db(). This function must NOT touch
      either counter for opens.

      Previously this function incremented one counter (routing via
      followup_status) while pixel_server incremented the other
      (routing via sent_email_type). They disagreed and hit DIFFERENT
      columns — both open_count AND followup_open_count went up by 1
      on every open. Removed all counter writes for opens here.

      log_event() is also NOT called for opens here because
      event_repository._update_outreach_lead() inside log_event()
      was the third place incrementing a counter.
      pixel_server._track_open_db() calls _record_lead_event()
      directly and owns the full open tracking pipeline.

    CLICKS:
      Analytics only — increments click_count.
      log_event() is called for event logging.

    REPLIES:
      Logs the event. Applies status fields only if lead is not
      already in a terminal reply state. Never increments reply_count
      — that is exclusively owned by gmail_watcher.

    SENT / CONVERTED / FAILED / OPT_OUT:
      Apply the appropriate state transition.
    """
    if lead_id is None:
        return {"status": "error", "message": "lead_id is required"}
    if campaign_id is None:
        return {"status": "error", "message": "campaign_id is required"}

    normalized = _normalize_event_type(event_type)
    metadata   = _safe_dict(metadata)

    # ── Opened ────────────────────────────────────────────────────────────
    if normalized == "opened":
        # ── FIX ───────────────────────────────────────────────────────────
        # Do NOT call log_event() here — event_repository._update_outreach_lead()
        # inside log_event() increments open_count or followup_open_count,
        # which would be a second increment on top of what pixel_server
        # already did in _track_open_db().
        #
        # Do NOT increment any counter here directly either — that was
        # the third increment path causing both columns to go up by 1.
        #
        # pixel_server._track_open_db() is the sole owner of all open
        # counter writes. It also calls _record_lead_event() directly
        # for event logging. Nothing else needs to happen here.
        # ──────────────────────────────────────────────────────────────────
        print(
            f"⏭️ event_router: open event skipped entirely → lead_id={lead_id} "
            f"(pixel_server._track_open_db is the sole owner)"
        )
        return {
            "status":     "success",
            "event_type": normalized,
            "route":      {
                "routed_to": "none",
                "action":    "skipped — pixel_server owns all open counter writes",
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
        return {"status": "error", "stage": "log_event", "message": str(e)}

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
            "route":      {"routed_to": "analytics_only", "action": "click_count_incremented"},
            "log":        log_result,
        }

    lead = _fetch_outreach_lead(lead_id)

    if not lead:
        return {
            "status":     "success",
            "event_type": normalized,
            "route":      {"routed_to": "none", "action": "lead_not_found"},
            "log":        log_result,
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
            "route":      {"routed_to": "lead_state", "action": "mark_sent"},
            "log":        log_result,
        }

    # ── Replied ───────────────────────────────────────────────────────────
    if normalized == "replied":
        if current_status in _REPLY_TERMINAL_STATUSES:
            print(
                f"📩 event_router: reply received but lead {lead_id} already "
                f"in terminal status={current_status} — log only, no state change"
            )
            return {
                "status":     "success",
                "event_type": normalized,
                "route":      {
                    "routed_to": "log_only",
                    "action":    "already_terminal_no_state_change",
                },
                "log":        log_result,
            }

        # Apply status fields only — reply_count is owned by gmail_watcher
        _update_outreach_lead(lead_id, {
            "status":          "replied",
            "followup_status": "completed",
            "next_followup":   None,
            "replied_at":      metadata.get("timestamp") or _now_iso(),
            "last_contacted":  metadata.get("timestamp") or _now_iso(),
            "last_updated":    _now_iso(),
            # reply_count intentionally NOT touched here
        })
        print(
            f"📩 event_router: reply status applied → lead_id={lead_id} "
            f"(reply_count managed by gmail_watcher)"
        )
        return {
            "status":     "success",
            "event_type": normalized,
            "route":      {
                "routed_to": "lead_state",
                "action":    "mark_replied_status_only",
            },
            "log":        log_result,
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
            "route":      {"routed_to": "lead_state", "action": f"mark_{normalized}"},
            "log":        log_result,
        }

    # ── Fallback ──────────────────────────────────────────────────────────
    return {
        "status":     "success",
        "event_type": normalized,
        "route":      {"routed_to": "unhandled", "action": "none"},
        "log":        log_result,
    }
