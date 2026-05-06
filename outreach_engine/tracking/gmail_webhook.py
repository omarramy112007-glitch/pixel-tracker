# outreach_engine/tracking/gmail_webhook.py

from __future__ import annotations

import asyncio
import base64
import json
import os
import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase

print("📨 GMAIL WEBHOOK MODULE LOADED")

router = APIRouter(prefix="/gmail", tags=["gmail-replies"])

PROCESS_LOCK = asyncio.Lock()
PROCESSED_MESSAGE_IDS = set()

BASE_DIR = Path(__file__).resolve().parents[2]
TOKEN_PATH = BASE_DIR / "token.pkl"
FALLBACK_HISTORY_FILE = BASE_DIR / "gmail_history_id.txt"

GMAIL_STATE_TABLE = os.getenv("GMAIL_STATE_TABLE", "gmail_state").strip()
GMAIL_USER_EMAIL = os.getenv("GMAIL_USER_EMAIL", "").strip().lower()


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
    token_bytes = _load_token_bytes_from_env()
    if token_bytes:
        creds = pickle.loads(token_bytes)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    if not TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"Missing Gmail token file: {TOKEN_PATH}. "
            "Set GMAIL_TOKEN_B64 or mount token.pkl."
        )

    with open(TOKEN_PATH, "rb") as f:
        creds = pickle.load(f)

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


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
                return str(history_id).strip()
    except Exception as e:
        print(f"⚠ Failed to load gmail_state history_id: {e}")

    if FALLBACK_HISTORY_FILE.exists():
        val = FALLBACK_HISTORY_FILE.read_text(encoding="utf-8").strip()
        if val and not val.startswith("{"):
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
                "updated_at": datetime.utcnow().isoformat(),
            }
        ).execute()
    except Exception as e:
        print(f"⚠ Failed to persist history_id in Supabase: {e}")

    try:
        FALLBACK_HISTORY_FILE.write_text(str(history_id).strip(), encoding="utf-8")
    except Exception as e:
        print(f"⚠ Failed to persist history_id to file: {e}")


def is_real_reply(msg) -> bool:
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
        or "re:" in subject[:12]
    )


def _find_lead(thread_id: str, sender_email: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    blocked_statuses = {"deleted", "archived"}

    if thread_id:
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
                    return int(row["id"]), int(row["campaign_id"])
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
                    return int(row["id"]), int(row["campaign_id"])
        except Exception as e:
            print("⚠ email lookup failed:", e)

    return None, None


def _reply_already_recorded(lead_id: int, msg_id: str) -> bool:
    try:
        res = (
            supabase.table("lead_events")
            .select("id, metadata, event_type, timestamp")
            .eq("lead_id", lead_id)
            .eq("event_type", "replied")
            .order("timestamp", desc=True)
            .limit(100)
            .execute()
        )

        for row in res.data or []:
            metadata = row.get("metadata") or {}
            if metadata.get("gmail_message_id") == msg_id:
                return True

    except Exception as e:
        print("⚠ reply dedupe check failed:", e)

    return False


def _process_reply_message(service, msg_id: str) -> bool:
    if not msg_id:
        return False

    if msg_id in PROCESSED_MESSAGE_IDS:
        return False

    msg = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="full",
    ).execute()

    headers = msg.get("payload", {}).get("headers", [])
    thread_id = msg.get("threadId") or ""

    from_raw = next(
        (x.get("value") for x in headers if (x.get("name") or "").lower() == "from"),
        "",
    )
    subject = next(
        (x.get("value") for x in headers if (x.get("name") or "").lower() == "subject"),
        "",
    )
    sender = _extract_email(from_raw)

    if GMAIL_USER_EMAIL and sender == GMAIL_USER_EMAIL:
        PROCESSED_MESSAGE_IDS.add(msg_id)
        return False

    reply_like = is_real_reply(msg)

    if not reply_like and not thread_id:
        PROCESSED_MESSAGE_IDS.add(msg_id)
        return False

    lead_id, campaign_id = _find_lead(thread_id, sender)
    if not lead_id or not campaign_id:
        print(f"⚠ No matching lead found for reply from {sender}")
        PROCESSED_MESSAGE_IDS.add(msg_id)
        return False

    if _reply_already_recorded(lead_id, msg_id):
        PROCESSED_MESSAGE_IDS.add(msg_id)
        return False

    metadata = {
        "gmail_message_id": msg_id,
        "thread_id": thread_id,
        "from": sender,
        "subject": subject,
        "channel": "gmail",
        "timestamp": datetime.utcnow().isoformat(),
    }

    result = store_event(
        lead_id=lead_id,
        event_type="replied",
        campaign_id=campaign_id,
        metadata=metadata,
    )

    PROCESSED_MESSAGE_IDS.add(msg_id)

    if isinstance(result, dict) and result.get("status") == "duplicate":
        return False

    print(f"✅ Reply saved → Lead {lead_id} | Campaign {campaign_id} | From {sender}")
    return True


async def _backfill_recent_messages(service, limit: int = 30) -> int:
    processed = 0
    try:
        res = service.users().messages().list(
            userId="me",
            labelIds=["INBOX"],
            maxResults=limit,
            q="newer_than:7d",
        ).execute()

        for item in res.get("messages", []):
            try:
                msg_id = item.get("id")
                if msg_id and _process_reply_message(service, msg_id):
                    processed += 1
            except Exception as e:
                print(f"⚠ Backfill message failed: {e}")

    except Exception as e:
        print(f"⚠ Backfill scan failed: {e}")

    return processed


@router.post("/webhook")
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
            if isinstance(new_history_id, dict) or new_history_id is None:
                print("❌ BAD historyId format received → ignoring")
                return {"status": "bad_history"}

            new_history_id = str(new_history_id).strip()
            print(f"📩 Incoming historyId: {new_history_id}")

            service = get_service()
            last_history_id = _load_last_history_id()

            if not last_history_id:
                print("⚠ No previous historyId → initializing and backfilling")
                _save_history_id(new_history_id)
                processed = await _backfill_recent_messages(service, limit=30)
                return {"status": "initialized", "processed": processed}

            try:
                processed = 0

                history = service.users().history().list(
                    userId="me",
                    startHistoryId=last_history_id,
                    historyTypes=["messageAdded"],
                ).execute()

                for h in history.get("history", []):
                    for m in h.get("messagesAdded", []):
                        msg_id = m.get("message", {}).get("id")
                        if not msg_id:
                            continue

                        if msg_id in PROCESSED_MESSAGE_IDS:
                            continue

                        try:
                            if _process_reply_message(service, msg_id):
                                processed += 1
                        except Exception as e:
                            print(f"⚠ Failed processing message {msg_id}: {e}")

            except HttpError as e:
                if e.resp.status == 404:
                    print("⚠ historyId expired → resetting and backfilling")
                    _save_history_id(new_history_id)
                    processed = await _backfill_recent_messages(service, limit=30)
                    return {"status": "reset_history", "processed": processed}
                raise

            _save_history_id(new_history_id)
            return {"status": "ok", "processed": processed}

        except Exception as e:
            print("❌ WEBHOOK ERROR:", e)
            return {"error": str(e)}


@router.get("/health")
def health():
    return {"status": "ok"}