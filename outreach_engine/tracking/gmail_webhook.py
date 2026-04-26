# outreach_engine/tracking/gmail_webhook.py

from __future__ import annotations

import asyncio
import base64
import json
import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, FastAPI, Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase

router = APIRouter()
PROCESS_LOCK = asyncio.Lock()
PROCESSED_MESSAGE_IDS = set()
PROCESSED_REPLY_THREADS = set()

BASE_DIR = Path(__file__).resolve().parents[2]
TOKEN_PATH = BASE_DIR / "token.pkl"
HISTORY_FILE = BASE_DIR / "gmail_history_id.txt"


def get_service():
    with open(TOKEN_PATH, "rb") as f:
        creds = pickle.load(f)
    return build("gmail", "v1", credentials=creds)


def _normalize(v: str) -> str:
    return (v or "").strip().lower()


def _extract_email(value: str) -> str:
    return _normalize(value.replace("<", " ").replace(">", " ").split()[-1])


def _load_last_history_id() -> Optional[str]:
    if not HISTORY_FILE.exists():
        return None
    val = HISTORY_FILE.read_text().strip()
    if not val or val.startswith("{"):
        return None
    return val


def _save_history_id(history_id: str):
    if not history_id:
        return
    HISTORY_FILE.write_text(str(history_id).strip())


def _message_exists(message_id: str) -> bool:
    if message_id in PROCESSED_MESSAGE_IDS:
        return True

    try:
        res = (
            supabase.table("lead_events")
            .select("id")
            .eq("metadata->>gmail_message_id", message_id)
            .limit(1)
            .execute()
        )
        if res.data:
            PROCESSED_MESSAGE_IDS.add(message_id)
            return True
    except Exception as e:
        print("⚠ message_exists error:", e)

    return False


def _reply_already_recorded(thread_id: str, msg_id: str) -> bool:
    if msg_id in PROCESSED_MESSAGE_IDS:
        return True
    if thread_id in PROCESSED_REPLY_THREADS:
        return True

    try:
        res = (
            supabase.table("lead_events")
            .select("id")
            .eq("event_type", "replied")
            .eq("metadata->>gmail_message_id", msg_id)
            .limit(1)
            .execute()
        )
        if res.data:
            PROCESSED_MESSAGE_IDS.add(msg_id)
            return True
    except Exception as e:
        print("⚠ reply_already_recorded msg-id check error:", e)

    if thread_id:
        try:
            res = (
                supabase.table("lead_events")
                .select("id")
                .eq("event_type", "replied")
                .eq("metadata->>thread_id", thread_id)
                .limit(1)
                .execute()
            )
            if res.data:
                PROCESSED_REPLY_THREADS.add(thread_id)
                return True
        except Exception as e:
            print("⚠ reply_already_recorded thread check error:", e)

    return False


def is_real_reply(msg) -> bool:
    headers = msg.get("payload", {}).get("headers", [])

    in_reply_to = None
    references = None
    subject = ""

    for h in headers:
        if h["name"] == "In-Reply-To":
            in_reply_to = h["value"]
        elif h["name"] == "References":
            references = h["value"]
        elif h["name"] == "Subject":
            subject = h["value"].lower()

    return bool(in_reply_to or references or subject.startswith("re:"))


def _find_lead(thread_id: str, sender_email: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    blocked_statuses = {"deleted", "archived"}

    try:
        res = (
            supabase.table("outreach_leads")
            .select("id, campaign_id, status")
            .eq("thread_id", thread_id)
            .limit(1)
            .execute()
        )
        if res.data:
            row = res.data[0]
            if (row.get("status") or "").lower() not in blocked_statuses:
                return row["id"], row["campaign_id"]
    except Exception as e:
        print("⚠ thread lookup failed:", e)

    if sender_email:
        try:
            res = (
                supabase.table("outreach_leads")
                .select("id, campaign_id, status")
                .ilike("email", sender_email)
                .limit(1)
                .execute()
            )
            if res.data:
                row = res.data[0]
                if (row.get("status") or "").lower() not in blocked_statuses:
                    return row["id"], row["campaign_id"]
        except Exception as e:
            print("⚠ email lookup failed:", e)

    return None, None


@router.post("/gmail/webhook")
async def gmail_webhook(request: Request):
    async with PROCESS_LOCK:
        print("🔥 GMAIL WEBHOOK HIT")

        try:
            body = await request.json()

            data = body.get("message", {}).get("data")
            if not data:
                return {"status": "ignored"}

            padding = "=" * (-len(data) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(data + padding).decode())

            new_history_id = decoded.get("historyId")
            if isinstance(new_history_id, dict):
                print("❌ BAD historyId format received → ignoring")
                return {"status": "bad_history"}

            new_history_id = str(new_history_id).strip()
            print(f"📩 Incoming historyId: {new_history_id}")

            last_history_id = _load_last_history_id()

            if not last_history_id:
                print("⚠ No previous historyId → initializing")
                _save_history_id(new_history_id)
                return {"status": "initialized"}

            service = get_service()

            try:
                history = service.users().history().list(
                    userId="me",
                    startHistoryId=last_history_id,
                    historyTypes=["messageAdded"],
                ).execute()

            except HttpError as e:
                if e.resp.status == 404:
                    print("⚠ historyId expired → resetting")
                    _save_history_id(new_history_id)
                    return {"status": "reset_history"}
                raise

            processed = 0

            for h in history.get("history", []):
                for m in h.get("messagesAdded", []):
                    msg_id = m["message"]["id"]
                    thread_id = m["message"]["threadId"]

                    if msg_id in PROCESSED_MESSAGE_IDS:
                        continue

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

                    print(f"📨 Reply from {sender} | {subject}")

                    lead_id, campaign_id = _find_lead(thread_id, sender)

                    if not lead_id:
                        print("⚠ No matching lead found")
                        PROCESSED_MESSAGE_IDS.add(msg_id)
                        continue

                    if _reply_already_recorded(thread_id, msg_id):
                        PROCESSED_MESSAGE_IDS.add(msg_id)
                        PROCESSED_REPLY_THREADS.add(thread_id)
                        continue

                    result = store_event(
                        lead_id=lead_id,
                        campaign_id=campaign_id,
                        event_type="replied",
                        metadata={
                            "gmail_message_id": msg_id,
                            "thread_id": thread_id,
                            "from": sender,
                            "subject": subject,
                            "channel": "gmail",
                        },
                    )

                    if result.get("status") == "duplicate":
                        PROCESSED_MESSAGE_IDS.add(msg_id)
                        PROCESSED_REPLY_THREADS.add(thread_id)
                        continue

                    print(f"✅ Reply saved → Lead {lead_id}")
                    processed += 1
                    PROCESSED_MESSAGE_IDS.add(msg_id)
                    PROCESSED_REPLY_THREADS.add(thread_id)

            _save_history_id(new_history_id)
            return {"status": "ok", "processed": processed}

        except Exception as e:
            print("❌ WEBHOOK ERROR:", e)
            return {"error": str(e)}


app = FastAPI()
app.include_router(router)