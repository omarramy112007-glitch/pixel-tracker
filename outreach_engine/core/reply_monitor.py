# outreach_engine/core/reply_monitor.py

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from fastapi import FastAPI, HTTPException, Request

from outreach_engine.database.supabase_client import (
    get_outreach_lead,
    get_outreach_lead_by_email_campaign,
    get_lead_by_email,
    insert_event,
    record_reply,
    supabase,
)
from outreach_engine.tracking.gmail_watcher import (
    POLL_INTERVAL_SECONDS,
    WATCH_MODE,
    check_for_replies,
    start_watch,
)

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


def _normalize_email(value: Any) -> str:
    return (str(value or "")).strip().lower()


def _find_outreach_lead(
    lead_id: Optional[Any] = None,
    email: Optional[str] = None,
    campaign_id: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    if lead_id is not None:
        try:
            row = get_outreach_lead(int(lead_id))
            if row and (campaign_id is None or _safe_int(row.get("campaign_id")) == _safe_int(campaign_id)):
                return row
        except Exception:
            pass

    if email:
        try:
            row = get_outreach_lead_by_email_campaign(email=email, campaign_id=_safe_int(campaign_id) if campaign_id is not None else None)
            if row:
                return row
        except Exception:
            pass

    return None


def _event_already_recorded(
    system_lead_id: Optional[str],
    message_id: Optional[str],
    thread_id: Optional[str],
) -> bool:
    if not system_lead_id:
        return False

    try:
        res = (
            supabase.table("lead_events")
            .select("id,metadata")
            .eq("lead_id", system_lead_id)
            .eq("event_type", "replied")
            .limit(200)
            .execute()
        )
        for row in res.data or []:
            meta = row.get("metadata") or {}
            if not isinstance(meta, dict):
                continue
            if message_id and meta.get("gmail_message_id") == message_id:
                return True
            if thread_id and meta.get("thread_id") == thread_id:
                return True
    except Exception as e:
        print(f"⚠️ duplicate check failed: {e}")

    return False


def process_reply_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Used by webhook / manual reply endpoint.
    This only records the reply.
    It does NOT send follow-ups here.

    The scheduler later decides:
      - followup_no_open
      - followup_soft_open
      - interested_followup
    based on status='sent' and counts.
    """
    lead_id = payload.get("lead_id")
    campaign_id = payload.get("campaign_id")
    email = _normalize_email(payload.get("email") or payload.get("from") or payload.get("sender"))
    message_id = payload.get("gmail_message_id") or payload.get("message_id")
    thread_id = payload.get("thread_id")
    subject = payload.get("subject") or ""
    body = payload.get("body") or payload.get("snippet") or ""
    timestamp = payload.get("timestamp") or _now_iso()

    lead = _find_outreach_lead(lead_id=lead_id, email=email, campaign_id=campaign_id)
    if not lead:
        return {
            "status": "not_found",
            "message": "No matching outreach lead found",
        }

    system_lead = get_lead_by_email(email) if email else None
    system_lead_id = str(system_lead["id"]) if system_lead and system_lead.get("id") else None

    if _event_already_recorded(system_lead_id, message_id, thread_id):
        return {
            "status": "duplicate",
            "message": "Reply already recorded",
            "lead_id": lead.get("id"),
            "campaign_id": lead.get("campaign_id"),
        }

    metadata = {
        "gmail_message_id": message_id,
        "thread_id": thread_id,
        "from": email,
        "subject": subject,
        "body": body,
        "timestamp": timestamp,
        "source": "reply_webhook",
        "campaign_id": campaign_id,
    }

    record_reply(
        lead_id=int(lead["id"]),
        campaign_id=int(lead["campaign_id"]),
        email=email,
        metadata=metadata,
    )

    if system_lead_id:
        try:
            insert_event({
                "lead_id": system_lead_id,
                "event_type": "replied",
                "metadata": metadata,
            })
        except Exception as e:
            print(f"⚠️ insert_event failed: {e}")

    return {
        "status": "ok",
        "lead_id": lead.get("id"),
        "campaign_id": lead.get("campaign_id"),
        "email": email,
        "timestamp": timestamp,
    }


def _poll_replies_once() -> List[Dict[str, Any]]:
    return check_for_replies()


async def _poll_loop(interval_seconds: int):
    while True:
        try:
            _poll_replies_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ reply poll loop error: {e}")
        await asyncio.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# FastAPI lifecycle
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "mode": GMAIL_WATCH_MODE}


@app.get("/check")
async def check_now():
    processed = _poll_replies_once()
    return {"status": "ok", "processed": len(processed), "replies": processed}


@app.post("/check")
async def check_now_post():
    processed = _poll_replies_once()
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
    Generic webhook entry point for replies.
    Accepts:
      - lead_id
      - campaign_id
      - email / from / sender
      - subject
      - body / snippet
      - thread_id
      - gmail_message_id
      - timestamp
    """
    payload = await request.json()
    result = process_reply_payload(payload)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result["message"])
    if result.get("status") == "duplicate":
        return result
    return result


if __name__ == "__main__":
    asyncio.run(_poll_loop(POLL_INTERVAL_SECONDS))
