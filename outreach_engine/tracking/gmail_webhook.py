from __future__ import annotations

import asyncio
import base64
import json
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase

print("📨 GMAIL WEBHOOK FILE LOADED FROM:", __file__)

router = APIRouter()

PROCESS_LOCK = asyncio.Lock()
PROCESSED_MESSAGE_IDS: set[str] = set()

BASE_DIR = Path(__file__).resolve().parents[2]
TOKEN_PATH = BASE_DIR / "token.pkl"
FALLBACK_HISTORY_FILE = BASE_DIR / "gmail_history_id.txt"

GMAIL_STATE_TABLE = os.getenv("GMAIL_STATE_TABLE", "gmail_state").strip()
GMAIL_USER_EMAIL = os.getenv("GMAIL_USER_EMAIL", "").strip().lower()

print("✅ Gmail router initialized")


@router.get("/ping")
async def gmail_ping():
    print("🏓 /gmail/ping HIT")
    return {
        "status": "ok",
        "service": "gmail router alive"
    }


@router.get("/webhook")
async def gmail_webhook_get():
    print("👀 GET /gmail/webhook HIT")
    return {
        "status": "ok",
        "message": "gmail webhook endpoint exists"
    }


@router.post("/webhook")
async def gmail_webhook_post(request: Request):
    print("🔥 POST /gmail/webhook HIT")
    return await process_gmail_webhook(request)


@router.get("/health")
async def gmail_health():
    return {
        "status": "ok",
        "service": "gmail webhook running",
    }


print("✅ Gmail webhook routes registered")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _normalize(value: str) -> str:
    return (value or "").strip().lower()


def _extract_email(value: str) -> str:
    cleaned = (value or "").replace("<", " ").replace(">", " ").strip()
    parts = cleaned.split()
    return _normalize(parts[-1] if parts else cleaned)


def _load_token_bytes_from_env() -> Optional[bytes]:
    token_b64 = os.getenv("GMAIL_TOKEN_B64", "").strip()

    if not token_b64:
        return None

    try:
        return base64.b64decode(token_b64)
    except Exception as e:
        print(f"⚠ Failed to decode GMAIL_TOKEN_B64: {e}")
        return None


def get_service():
    print("📨 Initializing Gmail service")

    token_bytes = _load_token_bytes_from_env()

    if token_bytes:
        print("✅ Using GMAIL_TOKEN_B64")
        creds = pickle.loads(token_bytes)

        return build(
            "gmail",
            "v1",
            credentials=creds,
            cache_discovery=False,
        )

    print(f"📁 Looking for token file: {TOKEN_PATH}")

    if not TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"Missing Gmail token file: {TOKEN_PATH}"
        )

    with open(TOKEN_PATH, "rb") as f:
        creds = pickle.load(f)

    print("✅ token.pkl loaded successfully")

    return build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False,
    )


def _load_last_history_id() -> Optional[str]:
    try:
        res = (
            supabase.table(GMAIL_STATE_TABLE)
            .select("history_id")
            .eq("id", 1)
            .limit(1)
            .execute()
        )

        if res.data:
            history_id = res.data[0].get("history_id")
            if history_id:
                print(f"📥 Loaded history_id: {history_id}")
                return str(history_id).strip()

    except Exception as e:
        print(f"⚠ Failed loading history_id: {e}")

    if FALLBACK_HISTORY_FILE.exists():
        val = FALLBACK_HISTORY_FILE.read_text(encoding="utf-8").strip()
        if val:
            return val

    return None


def _save_history_id(history_id: str) -> None:
    if not history_id:
        return

    try:
        supabase.table(GMAIL_STATE_TABLE).upsert(
            {
                "id": 1,
                "history_id": str(history_id).strip(),
                "updated_at": _utc_now_iso(),
            }
        ).execute()

        print(f"💾 Saved history_id: {history_id}")

    except Exception as e:
        print(f"⚠ Failed saving history_id: {e}")

    try:
        FALLBACK_HISTORY_FILE.write_text(
            str(history_id).strip(),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"⚠ Failed writing history file: {e}")


def is_real_reply(msg: Dict[str, Any]) -> bool:
    headers = msg.get("payload", {}).get("headers", [])

    subject = ""
    in_reply_to = False
    references = False

    for h in headers:
        name = (h.get("name") or "").strip().lower()
        value = h.get("value") or ""

        if name == "subject":
            subject = value.lower().strip()
        elif name == "in-reply-to":
            in_reply_to = True
        elif name == "references":
            references = True

    return (
        in_reply_to
        or references
        or subject.startswith("re:")
        or subject.startswith("fw:")
    )


def _find_lead(
    thread_id: str,
    sender_email: Optional[str],
) -> Tuple[Optional[int], Optional[int]]:
    if thread_id:
        try:
            res = (
                supabase.table("outreach_leads")
                .select("id, campaign_id")
                .eq("thread_id", thread_id)
                .limit(1)
                .execute()
            )

            if res.data:
                row = res.data[0]
                return int(row["id"]), int(row["campaign_id"])

        except Exception as e:
            print("⚠ thread lookup failed:", e)

    if sender_email:
        try:
            res = (
                supabase.table("outreach_leads")
                .select("id, campaign_id")
                .ilike("email", sender_email)
                .limit(1)
                .execute()
            )

            if res.data:
                row = res.data[0]
                return int(row["id"]), int(row["campaign_id"])

        except Exception as e:
            print("⚠ email lookup failed:", e)

    return None, None


def _reply_already_recorded(
    lead_id: int,
    msg_id: str,
) -> bool:
    try:
        res = (
            supabase.table("lead_events")
            .select("id, metadata")
            .eq("lead_id", lead_id)
            .eq("event_type", "replied")
            .limit(100)
            .execute()
        )

        for row in res.data or []:
            metadata = row.get("metadata") or {}
            if metadata.get("gmail_message_id") == msg_id:
                return True

    except Exception as e:
        print("⚠ reply dedupe failed:", e)

    return False


def _process_reply_message(service, msg_id: str) -> bool:
    if not msg_id:
        return False

    if msg_id in PROCESSED_MESSAGE_IDS:
        return False

    print(f"📨 Processing message: {msg_id}")

    msg = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="full",
    ).execute()

    headers = msg.get("payload", {}).get("headers", [])
    thread_id = msg.get("threadId") or ""

    from_raw = next(
        (
            x.get("value")
            for x in headers
            if (x.get("name") or "").lower() == "from"
        ),
        "",
    )

    subject = next(
        (
            x.get("value")
            for x in headers
            if (x.get("name") or "").lower() == "subject"
        ),
        "",
    )

    sender = _extract_email(from_raw)

    print(f"📩 Sender: {sender}")
    print(f"📩 Subject: {subject}")

    if GMAIL_USER_EMAIL and sender == GMAIL_USER_EMAIL:
        print("⚠ Ignoring self email")
        return False

    if not is_real_reply(msg):
        print("⚠ Not a reply")
        return False

    lead_id, campaign_id = _find_lead(thread_id, sender)

    if not lead_id or not campaign_id:
        print("⚠ No lead found")
        return False

    if _reply_already_recorded(lead_id, msg_id):
        print("⚠ Reply already recorded")
        return False

    metadata = {
        "gmail_message_id": msg_id,
        "thread_id": thread_id,
        "from": sender,
        "subject": subject,
        "channel": "gmail",
        "timestamp": _utc_now_iso(),
    }

    store_event(
        lead_id=lead_id,
        event_type="replied",
        campaign_id=campaign_id,
        metadata=metadata,
    )

    PROCESSED_MESSAGE_IDS.add(msg_id)

    print(f"✅ Reply saved for lead {lead_id}")

    return True


async def process_gmail_webhook(request: Request):
    async with PROCESS_LOCK:
        try:
            body = await request.json()

            print("📦 RAW BODY:", body)

            data = body.get("message", {}).get("data")

            if not data:
                print("⚠ No PubSub data")
                return {"status": "ignored"}

            padding = "=" * (-len(data) % 4)

            decoded = json.loads(
                base64.urlsafe_b64decode(
                    data + padding
                ).decode()
            )

            print("📨 DECODED:", decoded)

            new_history_id = str(
                decoded.get("historyId")
            ).strip()

            service = get_service()

            last_history_id = _load_last_history_id()

            if not last_history_id:
                _save_history_id(new_history_id)
                return {
                    "status": "initialized"
                }

            processed = 0

            history = service.users().history().list(
                userId="me",
                startHistoryId=last_history_id,
                historyTypes=["messageAdded"],
            ).execute()

            for h in history.get("history", []):
                for m in h.get("messagesAdded", []):
                    msg_id = (
                        m.get("message", {})
                        .get("id")
                    )

                    if not msg_id:
                        continue

                    try:
                        if _process_reply_message(service, msg_id):
                            processed += 1

                    except Exception as e:
                        print(f"⚠ Failed processing {msg_id}: {e}")

            _save_history_id(new_history_id)

            print(f"✅ Processed: {processed}")

            return {
                "status": "ok",
                "processed": processed,
            }

        except HttpError as e:
            print("❌ Gmail HTTP ERROR:", e)
            return {
                "status": "error",
                "error": str(e),
            }

        except Exception as e:
            print("❌ WEBHOOK ERROR:", e)
            return {
                "status": "error",
                "error": str(e),
            }