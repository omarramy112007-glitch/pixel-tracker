# outreach_engine/core/event_router.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import log_event

STOP_EVENTS       = {"converted", "failed", "opt_out", "completed"}
ANALYTICS_ONLY    = {"clicked"}

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_naive_iso() -> str:
    return datetime.utcnow().isoformat()


def _normalize_event_type(event_type: str) -> str:
    return _EVENT_ALIASES.get((event_type or "").strip().lower(), (event_type or "").strip().lower())


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
        supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
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

    Rules (matching the follow-up state machine):
      clicked  → analytics only, increment click_count, no routing
      opened   → increment open_count OR followup_open_count depending on followup_status
      replied  → stop automation, set followup_status='completed', status='replied'
      sent     → mark as sent
      converted / failed / opt_out → terminal states
    """
    if lead_id is None:
        return {"status": "error", "message": "lead_id is required"}
    if campaign_id is None:
        return {"status": "error", "message": "campaign_id is required"}

    normalized = _normalize_event_type(event_type)
    metadata   = _safe_dict(metadata)

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
            "route":      {"routed_to": "analytics_only", "action": "none"},
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

        if followup_status in {"no_open", "soft_open"}:
            # Open after a follow-up email → increment followup_open_count
            current = int(lead.get("followup_open_count") or 0)
            _update_outreach_lead(lead_id, {
                "followup_open_count": current + 1,
                "last_updated":        _now_iso(),
            })
            action = "increment_followup_open_count"
            print(f"📬 event_router: followup_open_count++ for lead_id={lead_id}")
        else:
            # Open after the initial email → increment open_count
            current = int(lead.get("open_count") or 0)
            payload: Dict[str, Any] = {
                "open_count":   current + 1,
                "email_opened": True,
                "last_updated": _now_iso(),
            }
            if not lead.get("email_opened_at"):
                payload["email_opened_at"] = _now_naive_iso()
            _update_outreach_lead(lead_id, payload)
            action = "increment_open_count"
            print(f"📬 event_router: open_count++ for lead_id={lead_id}")

        return {
            "status":     "success",
            "event_type": normalized,
            "route":      {"routed_to": "lead_state", "action": action},
            "log":        log_result,
        }

    # ── Replied ───────────────────────────────────────────────────────────
    if normalized == "replied":
        current_reply = int(lead.get("reply_count") or 0)
        # Reply is terminal — stop all automation immediately
        _update_outreach_lead(lead_id, {
            "reply_count":     current_reply + 1,
            "reply_status":    True,
            "status":          "replied",
            "followup_status": "completed",   # ← stops the state machine
            "next_followup":   None,
            "replied_at":      metadata.get("timestamp") or _now_iso(),
            "last_contacted":  metadata.get("timestamp") or _now_iso(),
            "last_updated":    _now_iso(),
        })
        return {
            "status":     "success",
            "event_type": normalized,
            "route":      {"routed_to": "lead_state", "action": "mark_replied_stop_automation"},
            "log":        log_result,
        }

    # ── Terminal: converted / failed / opt_out / completed ────────────────
    if normalized in {"converted", "failed", "opt_out", "completed"}:
        payload = {
            "status":       normalized,
            "last_updated": _now_iso(),
        }
        if normalized == "converted":
            payload["conversion_count"] = int(lead.get("conversion_count") or 0) + 1
        if normalized in {"failed", "opt_out"}:
            payload["followup_status"] = normalized  # also mark followup as terminal
            payload["next_followup"]   = None

        _update_outreach_lead(lead_id, payload)
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
