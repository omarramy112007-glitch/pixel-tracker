# outreach_engine/core/reply_monitor.py

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import store_event

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
    start_watch = lambda: {}        # noqa: E731
    GMAIL_WATCHER_AVAILABLE = False

app = FastAPI(title="Outreach Engine Reply Monitor")

GMAIL_WATCH_MODE = os.getenv("GMAIL_WATCH_MODE", WATCH_MODE).strip().lower()
POLL_TASK: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

# Keywords used for simple heuristic classification.
# Replace with an LLM call if you need higher accuracy.
_UNSUBSCRIBE_KEYWORDS = {
    "unsubscribe", "remove me", "stop emailing", "opt out", "opt-out",
    "take me off", "don't contact", "do not contact", "please remove",
}

_INTERESTED_KEYWORDS = {
    "interested", "tell me more", "sounds good", "let's talk", "let's connect",
    "book a call", "schedule a call", "would love to", "yes please", "sign me up",
    "more info", "send me", "i'd like", "i would like",
}

_QUESTION_KEYWORDS = {
    "how does", "how do", "what is", "what are", "can you explain",
    "could you", "could you explain", "do you", "does it", "is it",
}


def _classify_intent(subject: str, body: str) -> str:
    """
    Classify reply intent from subject + body text.

    Returns one of:
      - "unsubscribe"
      - "interested"
      - "question"
      - "not_interested"
      - "unknown"
    """
    text = f"{subject} {body}".lower()

    for kw in _UNSUBSCRIBE_KEYWORDS:
        if kw in text:
            return "unsubscribe"

    for kw in _INTERESTED_KEYWORDS:
        if kw in text:
            return "interested"

    for kw in _QUESTION_KEYWORDS:
        if kw in text:
            return "question"

    # Negative signals
    negative_signals = {"not interested", "no thanks", "not right now", "pass", "no thank you"}
    for kw in negative_signals:
        if kw in text:
            return "not_interested"

    return "unknown"


# ---------------------------------------------------------------------------
# Core: process a single reply event
# ---------------------------------------------------------------------------

def process_reply_event(
    lead_id: int,
    campaign_id: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Called by event_router when a 'replied' event is received.

    Steps:
      1. Classify reply intent from metadata
      2. Mark lead as replied (unconditionally)
      3. Cancel all pending follow-ups (unconditionally)
      4. Apply intent-specific action (mark interested, mark opt-out, etc.)
    """
    metadata = metadata or {}
    subject = str(metadata.get("subject") or "")
    body = str(metadata.get("body") or metadata.get("snippet") or "")
    intent = _classify_intent(subject, body)

    # Always mark replied first — this stops follow-ups via lead state
    _mark_lead_replied(lead_id, campaign_id, metadata, intent)

    # Always cancel scheduled follow-ups — unconditional
    _cancel_pending_followups(lead_id, campaign_id)

    # Apply intent-specific follow-on action
    intent_result = _apply_intent_action(lead_id, campaign_id, intent, metadata)

    return {
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "intent": intent,
        "intent_action": intent_result,
        "followups_cancelled": True,
    }


# ---------------------------------------------------------------------------
# State updates
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _mark_lead_replied(
    lead_id: int,
    campaign_id: int,
    metadata: Dict[str, Any],
    intent: str,
) -> None:
    """
    Update outreach_leads:
      - status = 'replied'
      - reply_count += 1
      - next_followup = None  (prevents scheduler from sending)
    """
    now = _now_iso()
    try:
        existing = (
            supabase.table("outreach_leads")
            .select("reply_count, status")
            .eq("id", lead_id)
            .eq("campaign_id", campaign_id)
            .limit(1)
            .execute()
        )

        if not existing.data:
            return

        row = existing.data[0]
        current_status = (row.get("status") or "").lower()

        # Don't overwrite terminal states more severe than replied
        if current_status in {"converted", "opt-out", "unsubscribed"}:
            return

        reply_count = _safe_int(row.get("reply_count"), 0)

        payload: Dict[str, Any] = {
            "status": "replied",
            "reply_count": reply_count + 1,
            "next_followup": None,          # ← kill the scheduled follow-up
            "last_updated": now,
            "metadata": {
                **(row.get("metadata") or {}),
                "reply_intent": intent,
                "reply_subject": metadata.get("subject"),
                "reply_sender": metadata.get("sender") or metadata.get("from"),
                "reply_timestamp": metadata.get("timestamp") or now,
                "reply_handled_at": now,
            },
        }

        supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()

    except Exception as e:
        print(f"⚠️ _mark_lead_replied failed for lead {lead_id}: {e}")


def _cancel_pending_followups(lead_id: int, campaign_id: int) -> None:
    """
    Unconditionally clear next_followup and any scheduler locks for this lead.
    This is the hard stop for follow-ups after a reply.
    """
    now = _now_iso()
    try:
        # Clear next_followup in outreach_leads
        supabase.table("outreach_leads").update({
            "next_followup": None,
            "last_updated": now,
        }).eq("id", lead_id).execute()
    except Exception as e:
        print(f"⚠️ _cancel_pending_followups (outreach_leads) failed: {e}")

    try:
        # Remove scheduler locks so no duplicate-send protection re-triggers
        supabase.table("scheduler_locks").delete().like(
            "lead_key", f"%{lead_id}%"
        ).execute()
    except Exception:
        # scheduler_locks table may not exist in all deployments — safe to ignore
        pass


def _apply_intent_action(
    lead_id: int,
    campaign_id: int,
    intent: str,
    metadata: Dict[str, Any],
) -> str:
    """
    Apply secondary state change based on classified intent.

    - interested   → mark pipeline_stage = Interested
    - unsubscribe  → mark status = opt-out
    - not_interested → keep 'replied', add pipeline note
    - question     → keep 'replied', flag for manual follow-up
    - unknown      → no extra action
    """
    now = _now_iso()

    if intent == "unsubscribe":
        try:
            supabase.table("outreach_leads").update({
                "status": "opt-out",
                "next_followup": None,
                "last_updated": now,
            }).eq("id", lead_id).execute()
        except Exception as e:
            print(f"⚠️ opt-out update failed: {e}")
        return "marked_opt_out"

    if intent == "interested":
        try:
            supabase.table("outreach_leads").update({
                "status": "interested",
                "last_updated": now,
            }).eq("id", lead_id).execute()

            # Also update system leads table pipeline stage
            _update_system_lead_intent(lead_id, "Interested", now)
        except Exception as e:
            print(f"⚠️ interested update failed: {e}")
        return "marked_interested"

    if intent == "question":
        try:
            # Flag for human review — don't auto-send anything
            supabase.table("outreach_leads").update({
                "metadata": {"needs_manual_reply": True, "intent": "question"},
                "last_updated": now,
            }).eq("id", lead_id).execute()
        except Exception:
            pass
        return "flagged_for_manual_reply"

    if intent == "not_interested":
        try:
            supabase.table("outreach_leads").update({
                "status": "completed",
                "next_followup": None,
                "last_updated": now,
            }).eq("id", lead_id).execute()
        except Exception:
            pass
        return "marked_completed"

    return "no_extra_action"


def _update_system_lead_intent(lead_id: int, stage: str, now: str) -> None:
    """Push pipeline stage to the main leads table when we have intent."""
    try:
        # Resolve system lead via outreach lead email
        outreach = (
            supabase.table("outreach_leads")
            .select("email")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if not outreach.data:
            return

        email = (outreach.data[0].get("email") or "").strip().lower()
        if not email:
            return

        supabase.table("leads").update({
            "pipeline_stage": stage,
            "updated_at": now,
        }).ilike("email", email).execute()
    except Exception as e:
        print(f"⚠️ _update_system_lead_intent failed: {e}")


# ---------------------------------------------------------------------------
# Batch processing (polling / webhook path)
# ---------------------------------------------------------------------------

def _normalize(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _extract_email(reply: Dict[str, Any]) -> Optional[str]:
    for key in ("email", "sender_email", "from_email", "sender", "from"):
        val = reply.get(key)
        if not val:
            continue
        text = str(val).strip()
        if "@" in text:
            return text.lower()
    return None


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


def _process_reply_item(reply: Any) -> Optional[Dict[str, Any]]:
    """Process a single raw reply dict from gmail_watcher."""
    if not isinstance(reply, dict):
        return None

    lead_id = reply.get("lead_id")
    campaign_id = reply.get("campaign_id")
    email = _extract_email(reply)

    row = _find_outreach_lead(lead_id=lead_id, email=email, campaign_id=campaign_id)
    if not row:
        return None

    current_status = _normalize(row.get("status"))
    # Already in a terminal state — skip
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

    # Also store a reply event for analytics
    try:
        store_event(
            lead_id=row["id"],
            campaign_id=row.get("campaign_id"),
            event_type="replied",
            metadata={
                "source": "gmail_reply_monitor",
                "channel": "email",
                **metadata,
            },
        )
    except Exception as e:
        print(f"⚠️ store_event(replied) failed: {e}")

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
# Background polling loop
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
# FastAPI lifecycle + routes
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


if __name__ == "__main__":
    asyncio.run(_poll_loop(POLL_INTERVAL_SECONDS))
