# outreach_engine/core/reply_monitor.py

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request

from outreach_engine.core.event_normalizer import normalize_event
from outreach_engine.core.reply_classifier import (
    AUTO_REPLY,
    INTERESTED,
    NOT_INTERESTED,
    QUESTION,
    classify_reply,
)
from outreach_engine.core.queue import add_lead_to_queue
from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import store_event

try:
    from outreach_engine.core.state_machine import transition as state_transition
except Exception:
    state_transition = None

try:
    from outreach_engine.tracking.gmail_watcher import (
        POLL_INTERVAL_SECONDS,
        WATCH_MODE,
        check_for_replies,
        start_watch,
    )
    GMAIL_WATCHER_AVAILABLE = True
except ImportError:
    POLL_INTERVAL_SECONDS = 300
    WATCH_MODE = "poll"
    check_for_replies = lambda: []  # noqa: E731
    start_watch = lambda: {}  # noqa: E731
    GMAIL_WATCHER_AVAILABLE = False

app = FastAPI(title="Outreach Engine Reply Monitor")

GMAIL_WATCH_MODE = os.getenv("GMAIL_WATCH_MODE", WATCH_MODE).strip().lower()
POLL_TASK: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _normalize_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _find_outreach_lead(
    lead_id: Optional[Any] = None,
    email: Optional[str] = None,
    campaign_id: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    try:
        query = supabase.table("outreach_leads").select("*")
        if lead_id is not None:
            query = query.eq("id", lead_id)
        elif email:
            query = query.ilike("email", email)
        if campaign_id is not None:
            query = query.eq("campaign_id", campaign_id)

        res = query.limit(1).execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        print(f"⚠️ outreach lead lookup failed: {e}")
    return None


def _update_outreach_lead_state(lead_id: Any, campaign_id: Any, payload: Dict[str, Any]) -> None:
    try:
        query = supabase.table("outreach_leads").update(payload).eq("id", lead_id)
        if campaign_id is not None:
            query = query.eq("campaign_id", campaign_id)
        query.execute()
    except Exception as e:
        print(f"⚠️ outreach_leads update failed: {e}")


def _update_system_lead_by_email(email: str, payload: Dict[str, Any]) -> None:
    if not email:
        return
    try:
        supabase.table("leads").update(payload).ilike("email", email).execute()
    except Exception as e:
        print(f"⚠️ leads update failed: {e}")


def _cancel_pending_followups(lead_id: Any, campaign_id: Any) -> None:
    now = _now_iso()

    try:
        supabase.table("outreach_leads").update({
            "next_followup": None,
            "last_updated": now,
        }).eq("id", lead_id).execute()
    except Exception as e:
        print(f"⚠️ clearing next_followup failed: {e}")

    try:
        supabase.table("scheduler_locks").delete().like("lead_key", f"{lead_id}:").execute()
    except Exception:
        pass


def _apply_state_machine_transition(
    lead_row: Dict[str, Any],
    event_type: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Uses the state machine if present. Falls back safely if not.
    """
    if state_transition is None:
        return {
            "from_state": lead_row.get("status"),
            "to_state": None,
            "changed": False,
            "stop_followups": False,
        }

    try:
        result = state_transition(lead_row, event_type, metadata=metadata)
        if isinstance(result, dict):
            return result
        return {
            "from_state": lead_row.get("status"),
            "to_state": getattr(result, "to_state", None),
            "changed": getattr(result, "changed", False),
            "stop_followups": getattr(result, "stop_followups", False),
        }
    except TypeError:
        # In case your state machine has a simpler signature
        try:
            result = state_transition(lead_row, event_type)
            if isinstance(result, dict):
                return result
            return {
                "from_state": lead_row.get("status"),
                "to_state": getattr(result, "to_state", None),
                "changed": getattr(result, "changed", False),
                "stop_followups": getattr(result, "stop_followups", False),
            }
        except Exception as e:
            print(f"⚠️ state machine transition failed: {e}")
            return {
                "from_state": lead_row.get("status"),
                "to_state": None,
                "changed": False,
                "stop_followups": False,
            }
    except Exception as e:
        print(f"⚠️ state machine transition failed: {e}")
        return {
            "from_state": lead_row.get("status"),
            "to_state": None,
            "changed": False,
            "stop_followups": False,
        }


def _queue_followup_for_intent(
    lead_row: Dict[str, Any],
    intent: str,
) -> Dict[str, Any]:
    """
    Queue a follow-up based on the reply intent.
    """
    lead_id = lead_row.get("id")
    step = int(lead_row.get("followup_step") or 0)

    if intent == INTERESTED:
        # Interested -> immediate next stage
        return add_lead_to_queue(
            lead_id=lead_id,
            followup_step=step + 1,
            delay_hours=0,
            reason="reply_interested",
        )

    if intent == QUESTION:
        # Questions usually need a fast but not instant human-safe follow-up
        return add_lead_to_queue(
            lead_id=lead_id,
            followup_step=step + 1,
            delay_hours=24,
            reason="reply_question",
        )

    return {"status": "no_queue_action", "intent": intent}


# ---------------------------------------------------------------------------
# Core reply processing
# ---------------------------------------------------------------------------

def process_reply_event(
    lead_id: Any,
    campaign_id: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Receives a reply, classifies it, updates state machine/state, and cancels follow-ups.
    """
    metadata = metadata or {}

    normalized_event = normalize_event(
        "gmail_webhook",
        {
            "event_type": "replied",
            "lead_id": lead_id,
            "campaign_id": campaign_id,
            "timestamp": metadata.get("timestamp") or _now_iso(),
            "metadata": metadata,
        },
    )

    subject = str(normalized_event["metadata"].get("subject") or "")
    body = str(
        normalized_event["metadata"].get("body")
        or normalized_event["metadata"].get("snippet")
        or normalized_event["metadata"].get("message")
        or ""
    )

    intent = classify_reply(subject=subject, body=body, metadata=normalized_event["metadata"])

    lead_row = _find_outreach_lead(lead_id=lead_id, campaign_id=campaign_id)
    if not lead_row:
        return {
            "lead_id": lead_id,
            "campaign_id": campaign_id,
            "status": "not_found",
            "intent": intent,
        }

    current_status = _normalize_status(lead_row.get("status"))
    if current_status in {"converted", "opt-out", "unsubscribed", "completed"}:
        return {
            "lead_id": lead_id,
            "campaign_id": campaign_id,
            "status": "skipped_terminal",
            "intent": intent,
        }

    # Update via state machine first
    transition_result = _apply_state_machine_transition(
        lead_row=lead_row,
        event_type="replied",
        metadata={
            **normalized_event["metadata"],
            "reply_intent": intent,
        },
    )

    # Hard-stop all follow-ups after any reply
    _cancel_pending_followups(lead_row["id"], lead_row.get("campaign_id"))

    now = _now_iso()
    base_update = {
        "reply_count": _safe_int(lead_row.get("reply_count"), 0) + 1,
        "status": "replied",
        "reply_status": True,
        "last_updated": now,
        "replied_at": now,
        "next_followup": None,
    }

    if intent == AUTO_REPLY:
        base_update["status"] = "sent"  # leave it in the active flow; it's not a human reply
        base_update["metadata"] = {
            **(lead_row.get("metadata") or {}),
            "reply_intent": AUTO_REPLY,
            "auto_reply": True,
            "reply_handled_at": now,
        }
    elif intent == INTERESTED:
        base_update["status"] = "interested"
        base_update["pipeline_stage"] = "Interested"
        base_update["metadata"] = {
            **(lead_row.get("metadata") or {}),
            "reply_intent": INTERESTED,
            "reply_handled_at": now,
        }
    elif intent == QUESTION:
        base_update["status"] = "replied"
        base_update["pipeline_stage"] = "Replied"
        base_update["metadata"] = {
            **(lead_row.get("metadata") or {}),
            "reply_intent": QUESTION,
            "needs_manual_reply": True,
            "reply_handled_at": now,
        }
    elif intent == NOT_INTERESTED:
        base_update["status"] = "completed"
        base_update["pipeline_stage"] = "Closed"
        base_update["metadata"] = {
            **(lead_row.get("metadata") or {}),
            "reply_intent": NOT_INTERESTED,
            "reply_handled_at": now,
        }

    _update_outreach_lead_state(lead_row["id"], lead_row.get("campaign_id"), base_update)

    # Mirror to system lead table if possible
    email = (lead_row.get("email") or "").strip().lower()
    if email:
        if intent == INTERESTED:
            _update_system_lead_by_email(email, {
                "pipeline_stage": "Interested",
                "reply_status": True,
                "updated_at": now,
            })
        elif intent == QUESTION:
            _update_system_lead_by_email(email, {
                "pipeline_stage": "Replied",
                "reply_status": True,
                "updated_at": now,
            })
        elif intent == NOT_INTERESTED:
            _update_system_lead_by_email(email, {
                "pipeline_stage": "Closed",
                "reply_status": True,
                "updated_at": now,
            })
        elif intent == AUTO_REPLY:
            _update_system_lead_by_email(email, {
                "reply_status": True,
                "updated_at": now,
            })

    # Queue the next step only for replies that are worth continuing
    queue_result = _queue_followup_for_intent(lead_row, intent)

    # Store analytics event
    try:
        store_event(
            lead_id=lead_row["id"],
            campaign_id=lead_row.get("campaign_id"),
            event_type="replied",
            metadata={
                **normalized_event["metadata"],
                "reply_intent": intent,
                "state_transition": transition_result,
                "channel": "email",
                "source": "reply_monitor",
            },
        )
    except Exception as e:
        print(f"⚠️ store_event(replied) failed: {e}")

    return {
        "lead_id": lead_row["id"],
        "campaign_id": lead_row.get("campaign_id"),
        "intent": intent,
        "state_transition": transition_result,
        "queue_result": queue_result,
        "followups_cancelled": True,
    }


# ---------------------------------------------------------------------------
# Batch reply processing
# ---------------------------------------------------------------------------

def _extract_email(reply: Dict[str, Any]) -> Optional[str]:
    for key in ("email", "sender_email", "from_email", "sender", "from"):
        val = reply.get(key)
        if not val:
            continue
        text = str(val).strip()
        if "@" in text:
            return text.lower()
    return None


def _process_reply_item(reply: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(reply, dict):
        return None

    lead_id = reply.get("lead_id")
    campaign_id = reply.get("campaign_id")
    email = _extract_email(reply)

    row = _find_outreach_lead(lead_id=lead_id, email=email, campaign_id=campaign_id)
    if not row:
        return None

    current_status = _normalize_status(row.get("status"))
    if current_status in {"replied", "completed", "converted", "failed", "opt-out", "unsubscribed", "interested"}:
        return {
            "lead_id": row.get("id"),
            "campaign_id": row.get("campaign_id"),
            "status": current_status,
            "skipped": True,
        }

    metadata = {
        "reply_source": "gmail",
        "subject": reply.get("subject"),
        "sender": reply.get("sender") or reply.get("from") or reply.get("email"),
        "timestamp": reply.get("timestamp") or _now_iso(),
        "body": reply.get("body") or reply.get("snippet") or "",
    }

    result = process_reply_event(
        lead_id=row["id"],
        campaign_id=row.get("campaign_id") or campaign_id,
        metadata=metadata,
    )

    return {
        "lead_id": row.get("id"),
        "campaign_id": row.get("campaign_id"),
        "email": row.get("email"),
        "intent": result.get("intent"),
        "status": "replied",
    }


def _check_and_process_replies() -> List[Dict[str, Any]]:
    raw = check_for_replies()
    if not raw:
        return []

    processed: List[Dict[str, Any]] = []
    for item in raw:
        result = _process_reply_item(item)
        if result:
            processed.append(result)

    return processed


# ---------------------------------------------------------------------------
# Background polling
# ---------------------------------------------------------------------------

async def _poll_loop(interval_seconds: int):
    while True:
        try:
            _check_and_process_replies()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ reply poll loop error: {e}")
        await asyncio.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# FastAPI lifecycle + endpoints
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    global POLL_TASK

    if GMAIL_WATCH_MODE == "watch":
        try:
            result = start_watch()
            print(f"✅ Gmail watch renewed on startup: {result}")
        except Exception as e:
            print(f"⚠️ Gmail watch failed, falling back to poll mode: {e}")
            if POLL_TASK is None:
                POLL_TASK = asyncio.create_task(_poll_loop(POLL_INTERVAL_SECONDS))
    else:
        print(f"👂 Starting reply polling every {POLL_INTERVAL_SECONDS}s")
        if POLL_TASK is None:
            POLL_TASK = asyncio.create_task(_poll_loop(POLL_INTERVAL_SECONDS))


@app.on_event("shutdown")
async def on_shutdown():
    global POLL_TASK
    if POLL_TASK:
        POLL_TASK.cancel()
        POLL_TASK = None


@app.get("/health")
async def health():
    return {"status": "ok", "mode": GMAIL_WATCH_MODE}


@app.get("/check")
async def check_now():
    processed = _check_and_process_replies()
    return {"status": "ok", "processed": len(processed), "replies": processed}


@app.post("/check")
async def check_now_post(request: Request):
    processed = _check_and_process_replies()
    return {"status": "ok", "processed": len(processed), "replies": processed}


@app.post("/renew-watch")
async def renew_watch():
    try:
        result = start_watch()
        return {"status": "ok", "watch": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/reply")
async def reply_webhook(request: Request):
    """
    Generic webhook entry point for replies if you wire Gmail/webhooks directly here.
    """
    payload = await request.json()
    normalized = normalize_event("gmail_webhook", payload)

    result = process_reply_event(
        lead_id=normalized.get("lead_id"),
        campaign_id=normalized.get("campaign_id"),
        metadata=normalized.get("metadata"),
    )

    return {"status": "ok", "result": result}


if __name__ == "__main__":
    asyncio.run(_poll_loop(POLL_INTERVAL_SECONDS))
