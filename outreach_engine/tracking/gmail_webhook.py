# outreach_engine/tracking/gmail_webhook.py

from __future__ import annotations

import asyncio
import base64
import json
import pickle
from pathlib import Path
from typing import Optional, Dict

from fastapi import APIRouter, FastAPI, Request
from googleapiclient.discovery import build

from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase

router = APIRouter()

PROCESS_LOCK = asyncio.Lock()
PROCESSED_MESSAGE_IDS = set()

TOKEN_PATH = Path(__file__).resolve().parents[2] / "token.pkl"


def get_service():
    with open(TOKEN_PATH, "rb") as f:
        creds = pickle.load(f)
    return build("gmail", "v1", credentials=creds)


def _normalize(v: str) -> str:
    return (v or "").strip().lower()


def _extract_email(value: str) -> str:
    return _normalize(value.replace("<", " ").replace(">", " ").split()[-1])


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
        print("message_exists error:", e)

    return False


def is_real_reply(msg) -> bool:
    headers = msg.get("payload", {}).get("headers", [])

    in_reply_to = None
    references = None
    subject = ""

    for h in headers:
        if h["name"] == "In-Reply-To":
            in_reply_to = h["value"]
        if h["name"] == "References":
            references = h["value"]
        if h["name"] == "Subject":
            subject = h["value"].lower()

    return bool(in_reply_to or references or subject.startswith("re:"))


def _find_lead(thread_id: str, sender_email: Optional[str]):
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
    except:
        pass

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
        except:
            pass

    return None, None


@router.post("/gmail/webhook")
async def gmail_webhook(request: Request):
    async with PROCESS_LOCK:
        body = await request.json()

        data = body.get("message", {}).get("data")
        if not data:
            return {"status": "ignored"}

        decoded = json.loads(base64.urlsafe_b64decode(data + "==").decode())
        history_id = decoded.get("historyId")

        service = get_service()

        history = service.users().history().list(
            userId="me",
            startHistoryId=str(history_id),
            historyTypes=["messageAdded"],
        ).execute()

        processed = 0

        for h in history.get("history", []):
            for m in h.get("messagesAdded", []):

                msg_id = m["message"]["id"]
                thread_id = m["message"]["threadId"]

                if _message_exists(msg_id):
                    continue

                msg = service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="full",
                ).execute()

                if not is_real_reply(msg):
                    PROCESSED_MESSAGE_IDS.add(msg_id)
                    continue

                headers = msg.get("payload", {}).get("headers", [])
                from_raw = next((x["value"] for x in headers if x["name"] == "From"), "")
                subject = next((x["value"] for x in headers if x["name"] == "Subject"), "")
                sender = _extract_email(from_raw)

                lead_id, campaign_id = _find_lead(thread_id, sender)
                if not lead_id:
                    continue

                store_event(
                    lead_id=lead_id,
                    campaign_id=campaign_id,
                    event_type="reply",
                    metadata={
                        "gmail_message_id": msg_id,
                        "thread_id": thread_id,
                        "from": sender,
                        "subject": subject,
                        "channel": "gmail",
                    },
                )

                processed += 1
                PROCESSED_MESSAGE_IDS.add(msg_id)

        return {"status": "ok", "processed": processed}


app = FastAPI()
app.include_router(router)