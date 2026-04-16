# outreach_engine/tracking/gmail_webhook.py

from __future__ import annotations

import asyncio
import base64
import json
import pickle
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, FastAPI, Request
from googleapiclient.discovery import build

from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase

router = APIRouter()
app = FastAPI(title="Outreach Engine Gmail Webhook")
app.include_router(router)

PROCESS_LOCK = asyncio.Lock()
PROCESSED_MESSAGE_IDS = set()

TOKEN_PATH = Path(__file__).resolve().parents[2] / "token.pkl"


# ---------------------------------------------------
# Gmail Service
# ---------------------------------------------------
def get_service():
    with open(TOKEN_PATH, "rb") as f:
        creds = pickle.load(f)
    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------
# Helpers
# ---------------------------------------------------
def _normalize_email(v: str) -> str:
    return (v or "").strip().lower()


def _extract_email_address(raw_value: str) -> str:
    """
    Turns:
      'Name <user@example.com>'
    into:
      'user@example.com'
    """
    _, addr = parseaddr(raw_value or "")
    return _normalize_email(addr or raw_value or "")


def _message_exists(message_id: str) -> bool:
    if message_id in PROCESSED_MESSAGE_IDS:
        return True

    try:
        res = (
            supabase.table("lead_events")
            .select("id")
            .eq("gmail_message_id", message_id)
            .limit(1)
            .execute()
        )
        if res.data:
            PROCESSED_MESSAGE_IDS.add(message_id)
            return True
    except Exception as e:
        print("⚠ message_exists error:", e)

    return False


def is_real_reply(message) -> bool:
    headers = message.get("payload", {}).get("headers", [])

    from_email = ""
    in_reply_to = ""
    references = ""
    subject = ""

    for h in headers:
        name = h.get("name")
        value = h.get("value", "")

        if name == "From":
            from_email = _normalize_email(value)
        elif name == "In-Reply-To":
            in_reply_to = value
        elif name == "References":
            references = value
        elif name == "Subject":
            subject = value.lower()

    if "noreply" in from_email:
        return False

    return bool(in_reply_to or references or subject.startswith("re:"))


def _find_lead(thread_id: str, sender_email: Optional[str] = None):
    """
    First try thread_id stored in outreach_leads.metadata.
    If that fails, fall back to sender email.
    """
    try:
        res = (
            supabase.table("outreach_leads")
            .select("id, campaign_id")
            .contains("metadata", {"thread_id": thread_id})
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]["id"], res.data[0]["campaign_id"]
    except Exception as e:
        print("⚠ find_lead(thread) error:", e)

    if sender_email:
        try:
            res = (
                supabase.table("outreach_leads")
                .select("id, campaign_id")
                .eq("email", sender_email)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]["id"], res.data[0]["campaign_id"]
        except Exception as e:
            print("⚠ find_lead(email) error:", e)

    return None, None


def _pixel_response():
    pixel = (
        b"GIF89a"
        b"\x01\x00\x01\x00"
        b"\x80\x00\x00"
        b"\x00\x00\x00"
        b"\xff\xff\xff"
        b"!\xf9\x04"
        b"\x01\x00\x00\x00\x00"
        b",\x00\x00\x00\x00"
        b"\x01\x00\x01\x00"
        b"\x00\x02\x02"
        b"D\x01\x00;"
    )

    from fastapi.responses import Response

    return Response(
        content=pixel,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ---------------------------------------------------
# MAIN WEBHOOK
# ---------------------------------------------------
@router.post("/gmail/webhook")
async def gmail_webhook(request: Request):
    async with PROCESS_LOCK:
        try:
            body = await request.json()
        except Exception:
            return {"status": "invalid_json"}

        message_data = body.get("message", {}).get("data")

        if not message_data:
            return {"status": "ignored"}

        try:
            padding = "=" * (-len(message_data) % 4)
            decoded = json.loads(
                base64.urlsafe_b64decode(message_data + padding).decode()
            )
        except Exception as e:
            print("⚠ decode error:", e)
            return {"status": "decode_failed"}

        history_id = decoded.get("historyId")
        if not history_id:
            return {"status": "no_history_id"}

        print(f"📩 Gmail webhook received | historyId={history_id}")

        service = get_service()

        try:
            history = service.users().history().list(
                userId="me",
                startHistoryId=str(history_id),
                historyTypes=["messageAdded"],
            ).execute()
        except Exception as e:
            print("❌ Gmail history fetch error:", e)
            return {"status": "history_error"}

        processed = 0

        for record in history.get("history", []):
            for msg in record.get("messagesAdded", []):
                message_id = msg["message"]["id"]
                thread_id = msg["message"]["threadId"]

                if _message_exists(message_id):
                    continue

                try:
                    message = service.users().messages().get(
                        userId="me",
                        id=message_id,
                        format="full",
                    ).execute()
                except Exception as e:
                    print("⚠ message fetch error:", e)
                    continue

                if not is_real_reply(message):
                    PROCESSED_MESSAGE_IDS.add(message_id)
                    continue

                headers = message.get("payload", {}).get("headers", [])
                from_raw = next((h["value"] for h in headers if h["name"] == "From"), "")
                subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
                from_email = _extract_email_address(from_raw)

                lead_id, campaign_id = _find_lead(thread_id, sender_email=from_email)
                if not lead_id:
                    continue

                result = store_event(
                    lead_id=lead_id,
                    campaign_id=campaign_id,
                    event_type="replied",
                    metadata={
                        "gmail_message_id": message_id,
                        "thread_id": thread_id,
                        "from_email": from_email,
                        "subject": subject,
                        "channel": "email",
                        "source": "gmail_webhook",
                    },
                )

                if result.get("status") in {"success", "duplicate"}:
                    processed += 1
                    PROCESSED_MESSAGE_IDS.add(message_id)

        return {"status": "ok", "processed": processed}


# ---------------------------------------------------
# Health Check
# ---------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------
# Run
# ---------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "outreach_engine.tracking.gmail_webhook:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
    )