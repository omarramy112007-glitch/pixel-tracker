# outreach_engine/tracking/gmail_watcher.py

from __future__ import annotations

import asyncio
import base64
import json
import os
import pickle
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Google libs are optional so Railway polling mode can still run
try:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GOOGLE_LIBS_AVAILABLE = True
except Exception:
    GoogleAuthRequest = None
    Credentials = None
    build = None
    HttpError = Exception
    GOOGLE_LIBS_AVAILABLE = False

from outreach_engine.core.reply_monitor import start_reply_polling
from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase

PROJECT_ID = os.getenv("GMAIL_PROJECT_ID", "make-487214").strip()
TOPIC_NAME = os.getenv("GMAIL_PUBSUB_TOPIC", "gmail-replies").strip()
TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.pkl").strip()
HISTORY_PATH = os.getenv("GMAIL_HISTORY_PATH", "gmail_history_id.txt").strip()

WATCH_MODE = os.getenv("GMAIL_WATCH_MODE", "poll").strip().lower()
POLL_INTERVAL_SECONDS = int(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "30"))
GMAIL_STATE_TABLE = os.getenv("GMAIL_STATE_TABLE", "gmail_state").strip()
GMAIL_USER_EMAIL = os.getenv("GMAIL_USER_EMAIL", "").strip().lower()

REQUIRED_WATCH_SCOPES = {
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.metadata",
}

PROCESS_LOCK = asyncio.Lock()
PROCESSED_MESSAGE_IDS: Dict[str, float] = {}
PROCESSED_MESSAGE_TTL_SECONDS = int(os.getenv("GMAIL_PROCESSED_MESSAGE_TTL", "86400"))


def _now_ts() -> float:
    return datetime.utcnow().timestamp()


def _purge_processed_cache() -> None:
    now = _now_ts()
    expired = [
        message_id
        for message_id, ts in PROCESSED_MESSAGE_IDS.items()
        if (now - ts) > PROCESSED_MESSAGE_TTL_SECONDS
    ]
    for message_id in expired:
        PROCESSED_MESSAGE_IDS.pop(message_id, None)


def _mark_processed(message_id: str) -> None:
    if not message_id:
        return
    _purge_processed_cache()
    PROCESSED_MESSAGE_IDS[message_id] = _now_ts()


def _was_processed(message_id: str) -> bool:
    if not message_id:
        return False
    _purge_processed_cache()
    return message_id in PROCESSED_MESSAGE_IDS


def _extract_email(value: str) -> str:
    """
    Extract a clean email from a Gmail From header value.
    Examples:
        "John Doe <john@gmail.com>" -> "john@gmail.com"
        "john@gmail.com" -> "john@gmail.com"
    """
    if not value:
        return ""

    raw = value.strip().lower()
    match = re.search(r"<([^>]+)>", raw)
    if match:
        return match.group(1).strip().lower()

    # fallback: keep only the part that looks like an email if possible
    email_match = re.search(r"([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,})", raw)
    if email_match:
        return email_match.group(1).strip().lower()

    return raw


def _load_credentials(token_path: str = TOKEN_PATH):
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError(
            "Google libraries are not installed. "
            "Install google-api-python-client and google-auth if you want Gmail watch mode."
        )

    token_b64 = os.getenv("GMAIL_TOKEN_B64", "").strip()
    if token_b64:
        try:
            data = base64.b64decode(token_b64)
            creds = pickle.loads(data)
            return creds
        except Exception as e:
            print(f"⚠ Failed to load GMAIL_TOKEN_B64 credentials: {e}")

    if not os.path.exists(token_path):
        raise FileNotFoundError(
            f"Missing OAuth token file: {token_path}. "
            "Run the Gmail auth step first to create it."
        )

    with open(token_path, "rb") as f:
        data = pickle.load(f)

    if isinstance(data, Credentials):
        return data

    if isinstance(data, dict):
        return Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=data.get("scopes"),
        )

    raise TypeError(
        f"Unsupported token format in {token_path}. "
        "Expected google.oauth2.credentials.Credentials or a dict."
    )


def _save_credentials(creds, token_path: str = TOKEN_PATH) -> None:
    with open(token_path, "wb") as f:
        pickle.dump(creds, f)


def _ensure_credentials_valid(creds):
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
            _save_credentials(creds)
            print("✅ Gmail credentials refreshed and saved.")
        except Exception as e:
            raise RuntimeError(f"Failed to refresh Gmail credentials: {e}") from e

    if not creds.valid:
        raise RuntimeError(
            "Gmail credentials are invalid. "
            "Re-run the Gmail OAuth flow to generate a fresh token."
        )

    return creds


def _build_topic_name(project_id: str, topic_name: str) -> str:
    if not topic_name:
        raise ValueError("GMAIL_PUBSUB_TOPIC is empty.")

    if topic_name.startswith("projects/"):
        return topic_name

    if not project_id:
        raise ValueError("GMAIL_PROJECT_ID is empty.")

    return f"projects/{project_id}/topics/{topic_name}"


def _persist_watch_response(response: Dict[str, Any], path: str = HISTORY_PATH) -> None:
    history_id = response.get("historyId")
    expiration = response.get("expiration")

    payload = {
        "historyId": history_id,
        "expiration": expiration,
    }

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(payload))
        print(f"💾 Watch metadata saved to {path}")
    except Exception as e:
        print(f"⚠ Failed to save watch metadata: {e}")


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

    if os.path.exists(HISTORY_PATH):
        try:
            val = Path(HISTORY_PATH).read_text(encoding="utf-8").strip()
            if val and not val.startswith("{"):
                return val
        except Exception as e:
            print(f"⚠ Failed to read fallback history file: {e}")

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
        Path(HISTORY_PATH).write_text(str(history_id).strip(), encoding="utf-8")
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

    if in_reply_to or references or subject.startswith("re:") or "re:" in subject[:12]:
        return True

    thread_id = msg.get("threadId") or ""
    return bool(thread_id)


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
            print("⚠ email lookup failed:", e)

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
        print("⚠ reply dedupe check failed:", e)

    return False


def _process_reply_message(service, msg_id: str) -> bool:
    if _was_processed(msg_id):
        return False

    msg = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="full",
    ).execute()

    if not is_real_reply(msg):
        _mark_processed(msg_id)
        return False

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
        _mark_processed(msg_id)
        return False

    lead_id, campaign_id = _find_lead(thread_id, sender)
    if not lead_id or not campaign_id:
        print(f"⚠ No matching lead found for reply from {sender}")
        _mark_processed(msg_id)
        return False

    if _reply_already_recorded(lead_id, thread_id, msg_id):
        _mark_processed(msg_id)
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

    if isinstance(result, dict) and result.get("status") == "duplicate":
        _mark_processed(msg_id)
        return False

    print(f"✅ Reply saved → Lead {lead_id} | Campaign {campaign_id} | From {sender}")
    _mark_processed(msg_id)
    return True


async def _backfill_recent_messages(service, limit: int = 100) -> int:
    processed = 0
    try:
        res = service.users().messages().list(
            userId="me",
            labelIds=["INBOX"],
            maxResults=limit,
            q="newer_than:30d",
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


def _load_watch_mode() -> str:
    return (os.getenv("GMAIL_WATCH_MODE", WATCH_MODE) or "poll").strip().lower()


def start_watch() -> Dict[str, Any]:
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError(
            "Google libraries are not installed. "
            "Switch GMAIL_WATCH_MODE=poll or install Google auth packages."
        )

    creds = _ensure_credentials_valid(_load_credentials())
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    request_body = {
        "labelIds": ["INBOX"],
        "topicName": _build_topic_name(PROJECT_ID, TOPIC_NAME),
    }

    try:
        response = service.users().watch(userId="me", body=request_body).execute()

        print("👀 Watch started:", response)
        _persist_watch_response(response)

        scopes = set(creds.scopes or [])
        if scopes and not (scopes & REQUIRED_WATCH_SCOPES):
            print(
                "⚠ Your token scopes may be too limited for Gmail watch. "
                "If watch fails later, re-auth with gmail.modify."
            )

        return response

    except HttpError as e:
        content = getattr(e, "content", None)
        if content:
            print(f"❌ Gmail watch failed: {content}")
        else:
            print(f"❌ Gmail watch failed: {e}")
        raise

    except Exception as e:
        print(f"❌ Unexpected error while starting Gmail watch: {e}")
        raise


async def main():
    mode = _load_watch_mode()

    if mode == "watch":
        start_watch()
        return

    await start_reply_polling(interval_seconds=POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())