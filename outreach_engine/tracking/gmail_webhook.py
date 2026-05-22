# outreach_engine/tracking/gmail_webhook.py

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Request

try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GOOGLE_LIBS_AVAILABLE = True
except Exception:
    build = None
    HttpError = Exception
    GOOGLE_LIBS_AVAILABLE = False

from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.gmail_auth import authenticate

router = APIRouter()

PROCESS_LOCK = asyncio.Lock()
PROCESSED_MESSAGE_IDS: set[str] = set()

BASE_DIR = Path(__file__).resolve().parents[2]
FALLBACK_HISTORY_FILE = BASE_DIR / "gmail_history_id.txt"

GMAIL_USER_EMAIL = os.getenv("GMAIL_USER_EMAIL", "").strip().lower()
DEBUG_LOGS = os.getenv("GMAIL_DEBUG_LOGS", "false").strip().lower() == "true"

IGNORED_DOMAINS = {
    "notify.railway.app",
    "github.com",
    "redditmail.com",
    "discover.pinterest.com",
    "pinterest.com",
    "quora.com",
    "coursera.org",
    "coursera.com",
    "apollo.io",
    "stockanalysis.com",
    "talabat.com",
    "mail.theresanaiforthat.com",
}

IGNORED_PREFIXES = (
    "noreply@",
    "no-reply@",
    "donotreply@",
    "do-not-reply@",
    "hello@notify.",
)


def log(*args, force: bool = False) -> None:
    if force or DEBUG_LOGS:
        print(*args)


@router.get("/ping")
async def gmail_ping():
    return {"status": "ok", "service": "gmail router alive"}


@router.get("/webhook")
async def gmail_webhook_get():
    return {"status": "ok", "message": "gmail webhook endpoint exists"}


@router.post("/webhook")
async def gmail_webhook_post(request: Request):
    return await process_gmail_webhook(request)


@router.get("/health")
async def gmail_health():
    return {"status": "ok", "service": "gmail webhook running"}


def _utc_now() -> datetime:
    return datetime.utcnow()


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _normalize(value: str) -> str:
    return (value or "").strip().lower()


def _extract_email(value: str) -> str:
    cleaned = (value or "").replace("<", " ").replace(">", " ").strip()
    parts = cleaned.split()
    if not parts:
        return _normalize(cleaned)

    candidate = parts[-1]
    email_match = re.search(
        r"([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,})",
        candidate.lower(),
    )
    if email_match:
        return email_match.group(1).strip().lower()

    return _normalize(candidate)


def _is_ignored_sender(sender: str) -> bool:
    sender = _normalize(sender)
    if not sender:
        return True

    if sender.startswith(IGNORED_PREFIXES):
        return True

    domain = sender.split("@")[-1] if "@" in sender else ""
    return domain in IGNORED_DOMAINS


def get_service():
    if not GOOGLE_LIBS_AVAILABLE or build is None:
        raise RuntimeError(
            "googleapiclient is not installed. Install google-api-python-client."
        )

    creds = authenticate()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _load_last_history_id() -> Optional[str]:
    if FALLBACK_HISTORY_FILE.exists():
        try:
            val = FALLBACK_HISTORY_FILE.read_text(encoding="utf-8").strip()
            if val and not val.startswith("{"):
                return val
        except Exception as e:
            log(f"⚠ Failed reading fallback history file: {e}", force=True)

    return None


def _save_history_id(history_id: str) -> None:
    if not history_id:
        return

    try:
        FALLBACK_HISTORY_FILE.write_text(str(history_id).strip(), encoding="utf-8")
    except Exception as e:
        log(f"⚠ Failed writing history file: {e}", force=True)


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

    if in_reply_to or references:
        return True

    if subject.startswith("re:") or subject.startswith("fw:") or "re:" in subject[:12]:
        return True

    return False


def _find_lead(thread_id: str, sender_email: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    blocked_statuses = {"deleted", "archived"}

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
            log(f"⚠ email lookup failed: {e}", force=True)

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
            log(f"⚠ thread lookup failed: {e}", force=True)

    return None, None


def _reply_already_recorded(lead_id: int, thread_id: str, msg_id: str) -> bool:
    try:
        res = (
            supabase.table("lead_events")
            .select("id, metadata, event_type, timestamp")
            .eq("lead_id", lead_id)
            .eq("event_type", "replied")
            .order("timestamp", desc=True)
            .limit(200)
            .execute()
        )

        for row in res.data or []:
            metadata = row.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue

            if metadata.get("gmail_message_id") == msg_id:
                return True

            if thread_id and metadata.get("thread_id") == thread_id:
                return True

    except Exception as e:
        log(f"⚠ reply dedupe check failed: {e}", force=True)

    return False


def _process_reply_message(service, msg_id: str) -> bool:
    if not msg_id or msg_id in PROCESSED_MESSAGE_IDS:
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

    if not sender or _is_ignored_sender(sender) or (GMAIL_USER_EMAIL and sender == GMAIL_USER_EMAIL):
        PROCESSED_MESSAGE_IDS.add(msg_id)
        return False

    if not is_real_reply(msg):
        PROCESSED_MESSAGE_IDS.add(msg_id)
        return False

    lead_id, campaign_id = _find_lead(thread_id, sender)
    if not lead_id or not campaign_id:
        PROCESSED_MESSAGE_IDS.add(msg_id)
        return False

    if _reply_already_recorded(lead_id, thread_id, msg_id):
        PROCESSED_MESSAGE_IDS.add(msg_id)
        return False

    metadata = {
        "gmail_message_id": msg_id,
        "thread_id": thread_id,
        "from": sender,
        "subject": subject,
        "channel": "gmail",
        "timestamp": _utc_now_iso(),
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

    log(f"✅ Reply saved → Lead {lead_id} | Campaign {campaign_id} | From {sender}", force=True)
    return True


async def process_gmail_webhook(request: Request):
    async with PROCESS_LOCK:
        try:
            try:
                body = await request.json()
            except Exception as e:
                log(f"⚠ Webhook received empty or invalid JSON body: {e}", force=True)
                return {"status": "ignored"}

            data = body.get("message", {}).get("data")
            if not data:
                return {"status": "ignored"}

            padding = "=" * (-len(data) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(data + padding).decode())

            new_history_id = str(decoded.get("historyId") or "").strip()
            if not new_history_id:
                return {"status": "ignored"}

            service = get_service()
            last_history_id = _load_last_history_id()

            if not last_history_id:
                _save_history_id(new_history_id)
                return {"status": "initialized"}

            processed = 0

            try:
                history = service.users().history().list(
                    userId="me",
                    startHistoryId=last_history_id,
                    historyTypes=["messageAdded"],
                ).execute()
            except HttpError as e:
                if getattr(e.resp, "status", None) == 404:
                    _save_history_id(new_history_id)
                    return {"status": "reset_history"}
                raise

            for h in history.get("history", []):
                for m in h.get("messagesAdded", []):
                    msg_id = m.get("message", {}).get("id")
                    if not msg_id:
                        continue

                    try:
                        if _process_reply_message(service, msg_id):
                            processed += 1
                    except Exception as e:
                        log(f"⚠ Failed processing {msg_id}: {e}", force=True)

            _save_history_id(new_history_id)

            return {"status": "ok", "processed": processed}

        except HttpError as e:
            log(f"❌ Gmail HTTP ERROR: {e}", force=True)
            return {"status": "error", "error": str(e)}

        except Exception as e:
            log(f"❌ WEBHOOK ERROR: {e}", force=True)
            return {"status": "error", "error": str(e)}
