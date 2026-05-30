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

# Statuses where we still allow open tracking but don't change
# the lead's automation state any further
_OPEN_ALLOWED_STATUSES = {
    "sent", "replied", "new", "pending",
    "not_contacted", "contacted",
    "followup_no_open", "followup_soft_open",
    "interested_followup",
}

# Statuses where the reply event should only log — not re-apply
# state changes (because gmail_watcher already did it)
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

    Open tracking:
      - Always allowed regardless of lead status (replied leads can
        still open emails — we just don't change their automation state).
      - Routes to open_count or followup_open_count based on followup_status.
      - Does NOT touch reply_count or automation status.

    Reply tracking:
      - Logs the event always.
      - Only applies status changes if lead is NOT already in a terminal
        reply state — because gmail_watcher already incremented reply_count
        and set the status. This prevents the double-count.
      - Never increments reply_count here — that is exclusively owned
        by gmail_watcher._increment_reply_count_and_finalize().

    Click tracking:
      - Analytics only, increments click_count.

    Sent / converted / failed / opt_out:
      - Apply the appropriate terminal or transition state.
    """
    if lead_id is None:
        return {"status": "error", "message": "lead_id is required"}
    if campaign_id is None:
        return {"status": "error", "message": "campaign_id is required"}

    normalized = _normalize_event_type(event_type)
    metadata   = _safe_dict(metadata)

    # Always log the raw event regardless of what we do with the state
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

    # ── Opened ────────────────────────────────────────────────────────────
    if normalized == "opened":
        followup_status = (lead.get("followup_status") or "").strip().lower()

        # Allow open tracking even for replied leads — they opened the email
        # and we want the count to be accurate. We just don't change their
        # automation state (no status update, no next_followup change).
        #
        # Route to the correct counter:
        #   followup_status in {no_open, soft_open} → followup_open_count
        #   everything else (including replied)      → open_count
        if followup_status in {"no_open", "soft_open"}:
            current = int(lead.get("followup_open_count") or 0)
            _update_outreach_lead(lead_id, {
                "followup_open_count": current + 1,
                "last_updated":        _now_iso(),
            })
            action = "increment_followup_open_count"
            print(
                f"📬 event_router: followup_open_count++ → lead_id={lead_id} "
                f"(followup_status={followup_status})"
            )
        else:
            current         = int(lead.get("open_count") or 0)
            open_payload: Dict[str, Any] = {
                "open_count":   current + 1,
                "email_opened": True,
                "last_updated": _now_iso(),
            }
            if not lead.get("email_opened_at"):
                open_payload["email_opened_at"] = _now_naive_iso()
            _update_outreach_lead(lead_id, open_payload)
            action = "increment_open_count"
            print(
                f"📬 event_router: open_count++ → lead_id={lead_id} "
                f"(status={current_status}, followup_status={followup_status})"
            )

        return {
            "status":     "success",
            "event_type": normalized,
            "route":      {"routed_to": "lead_state", "action": action},
            "log":        log_result,
        }

    # ── Replied ───────────────────────────────────────────────────────────
    if normalized == "replied":
        # ── FIX: do NOT increment reply_count here ────────────────────────
        # reply_count is exclusively incremented by
        # gmail_watcher._increment_reply_count_and_finalize().
        # If we also increment here we get a double-count on every reply.
        #
        # What we DO here:
        #   - If lead is already in a terminal reply state → log only,
        #     no state changes (idempotent, prevents redundant DB writes).
        #   - If lead is NOT yet marked replied → apply the status fields
        #     (this covers the webhook path where watcher may not have run yet).

        if current_status in _REPLY_TERMINAL_STATUSES:
            # Already handled by gmail_watcher — just confirm the log
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

        # Lead not yet marked replied — apply status fields only.
        # reply_count will be incremented by gmail_watcher separately.
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
