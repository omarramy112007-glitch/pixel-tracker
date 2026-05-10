# outreach_engine/tracking/gmail_watcher.py

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

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

from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.gmail_auth import authenticate

PROJECT_ID = os.getenv("GMAIL_PROJECT_ID", "make-487214").strip()
TOPIC_NAME = os.getenv("GMAIL_PUBSUB_TOPIC", "gmail-replies").strip()

ROOT_DIR = Path(__file__).resolve().parents[2]
TOKEN_JSON_PATH = Path(os.getenv("GMAIL_TOKEN_JSON_PATH", str(ROOT_DIR / "token.json")))

WATCH_MODE = os.getenv("GMAIL_WATCH_MODE", "poll").strip().lower()
POLL_INTERVAL_SECONDS = int(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "30"))
GMAIL_USER_EMAIL = os.getenv("GMAIL_USER_EMAIL", "").strip().lower()
DEBUG_LOGS = os.getenv("GMAIL_DEBUG_LOGS", "false").strip().lower() == "true"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

REQUIRED_WATCH_SCOPES = {
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.metadata",
}

PROCESS_LOCK = asyncio.Lock()
PROCESSED_MESSAGE_IDS: Dict[str, float] = {}
PROCESSED_MESSAGE_TTL_SECONDS = int(os.getenv("GMAIL_PROCESSED_MESSAGE_TTL", "86400"))

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


def _normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def _extract_email(value: str) -> str:
    if not value:
        return ""

    raw = value.strip().lower()
    match = re.search(r"<([^>]+)>", raw)
    if match:
        return match.group(1).strip().lower()

    email_match = re.search(r"([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,})", raw)
    if email_match:
        return email_match.group(1).strip().lower()

    return raw


def _is_ignored_sender(sender: str) -> bool:
    sender = (sender or "").strip().lower()
    if not sender:
        return True

    if sender.startswith(IGNORED_PREFIXES):
        return True

    domain = sender.split("@")[-1] if "@" in sender else ""
    return domain in IGNORED_DOMAINS


def _load_credentials_from_b64_env():
    token_b64 = os.getenv("GMAIL_TOKEN_B64", "").strip()
    if not token_b64:
        return None

    try:
        decoded_bytes = base64.b64decode(token_b64)
        token_json = decoded_bytes.decode("utf-8")
        token_info = json.loads(token_json)

        if not isinstance(token_info, dict):
            raise ValueError("Decoded GMAIL_TOKEN_B64 is not a JSON object")

        if not GOOGLE_LIBS_AVAILABLE or Credentials is None:
            raise RuntimeError("google libraries missing")

        creds = Credentials.from_authorized_user_info(token_info, scopes=SCOPES)
        log("✅ Using GMAIL_TOKEN_B64 (JSON)", force=True)
        return creds

    except Exception as e:
        log(f"⚠ Failed to load GMAIL_TOKEN_B64 credentials: {e}", force=True)
        return None


def _load_credentials_from_file():
    if not GOOGLE_LIBS_AVAILABLE or Credentials is None:
        return None

    if TOKEN_JSON_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_JSON_PATH), SCOPES)
            log(f"📄 Loaded Gmail token.json from: {TOKEN_JSON_PATH}", force=True)
            return creds
        except Exception as e:
            log(f"⚠ Failed to load token.json: {e}", force=True)

    return None


def _load_credentials_raw():
    creds = _load_credentials_from_b64_env()
    if creds is not None:
        return creds

    creds = _load_credentials_from_file()
    if creds is not None:
        return creds

    return authenticate()


def _coerce_credentials(raw):
    if not GOOGLE_LIBS_AVAILABLE or Credentials is None:
        raise RuntimeError(
            "Google libraries are not installed. "
            "Install google-api-python-client and google-auth."
        )

    if isinstance(raw, Credentials):
        return raw

    if isinstance(raw, dict):
        return Credentials.from_authorized_user_info(raw, scopes=SCOPES)

    raise TypeError(f"Unsupported Gmail credentials type: {type(raw)!r}")


def _save_credentials(creds) -> None:
    try:
        if hasattr(creds, "to_json"):
            TOKEN_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_JSON_PATH.write_text(creds.to_json(), encoding="utf-8")
            log(f"💾 Saved Gmail token.json to: {TOKEN_JSON_PATH}", force=True)
    except Exception as e:
        log(f"⚠ Failed to save token.json: {e}", force=True)


def _ensure_credentials_valid(creds):
    if (
        hasattr(creds, "expired")
        and getattr(creds, "expired", False)
        and getattr(creds, "refresh_token", None)
    ):
        try:
            if GoogleAuthRequest is None:
                raise RuntimeError("Google auth request class unavailable")
            creds.refresh(GoogleAuthRequest())
            _save_credentials(creds)
            log("✅ Gmail credentials refreshed and saved.", force=True)
        except Exception as e:
            raise RuntimeError(f"Failed to refresh Gmail credentials: {e}") from e

    if not getattr(creds, "valid", True):
        raise RuntimeError(
            "Gmail credentials are invalid. "
            "Re-run the Gmail OAuth flow to generate a fresh token."
        )

    return creds


def get_service():
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError(
            "Google libraries are not installed. "
            "Install google-api-python-client and google-auth if you want Gmail watch mode."
        )

    raw = _load_credentials_raw()
    creds = _ensure_credentials_valid(_coerce_credentials(raw))
    _save_credentials(creds)

    return build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False,
    )


def _build_topic_name(project_id: str, topic_name: str) -> str:
    if not topic_name:
        raise ValueError("GMAIL_PUBSUB_TOPIC is empty.")

    if topic_name.startswith("projects/"):
        return topic_name

    if not project_id:
        raise ValueError("GMAIL_PROJECT_ID is empty.")

    return f"projects/{project_id}/topics/{topic_name}"


def _is_reply_headers(headers: List[Dict[str, Any]]) -> bool:
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


def _message_sender(msg: Dict[str, Any]) -> str:
    headers = msg.get("payload", {}).get("headers", [])
    from_raw = next(
        (x.get("value") for x in headers if (x.get("name") or "").lower() == "from"),
        "",
    )
    return _extract_email(from_raw)


def _message_subject(msg: Dict[str, Any]) -> str:
    headers = msg.get("payload", {}).get("headers", [])
    return next(
        (x.get("value") for x in headers if (x.get("name") or "").lower() == "subject"),
        "",
    )


def _thread_has_reply(thread: Dict[str, Any], lead_email: str) -> Optional[Dict[str, Any]]:
    messages = thread.get("messages", []) or []
    if len(messages) < 2:
        return None

    ordered = sorted(
        messages,
        key=lambda m: int(m.get("internalDate") or 0),
    )

    our_seen = False
    candidate: Optional[Dict[str, Any]] = None

    for msg in ordered:
        sender = _message_sender(msg)
        headers = msg.get("payload", {}).get("headers", [])

        if GMAIL_USER_EMAIL and sender == GMAIL_USER_EMAIL:
            our_seen = True
            continue

        if sender != lead_email:
            continue

        is_reply_marked = _is_reply_headers(headers)

        if our_seen:
            candidate = msg
            continue

        if is_reply_marked:
            candidate = msg

    if candidate is None:
        return None

    if GMAIL_USER_EMAIL and _message_sender(candidate) == GMAIL_USER_EMAIL:
        return None

    return {
        "gmail_message_id": candidate.get("id"),
        "thread_id": thread.get("id") or candidate.get("threadId") or "",
        "from": lead_email,
        "subject": _message_subject(candidate),
        "timestamp": datetime.utcnow().isoformat(),
    }


def _lead_is_eligible(lead: Dict[str, Any]) -> bool:
    status = (lead.get("status") or "").strip().lower()
    last_email_sent = lead.get("last_email_sent")
    email = _normalize_email(lead.get("email") or "")

    if not email:
        return False

    if _is_ignored_sender(email):
        return False

    if status in {"converted", "won", "lost", "closed", "archived", "deleted"}:
        return False

    return bool(last_email_sent or lead.get("thread_id") or status in {"sent", "replied"})


def _fetch_candidate_leads(limit: int = 300) -> List[Dict[str, Any]]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .order("last_updated", desc=True)
            .limit(limit)
            .execute()
        )
        leads = res.data or []
        return [lead for lead in leads if _lead_is_eligible(lead)]
    except Exception as e:
        log(f"⚠ Failed to fetch candidate leads: {e}", force=True)
        return []


def _candidate_thread_ids_for_lead(service, lead: Dict[str, Any]) -> List[str]:
    thread_id = (lead.get("thread_id") or "").strip()
    if thread_id:
        return [thread_id]

    email = _normalize_email(lead.get("email") or "")
    if not email:
        return []

    try:
        res = service.users().threads().list(
            userId="me",
            q=f"from:{email} newer_than:30d",
            maxResults=10,
        ).execute()

        return [
            t.get("id")
            for t in (res.get("threads") or [])
            if t.get("id")
        ]
    except Exception as e:
        log(f"⚠ Thread search failed for {email}: {e}", force=True)
        return []


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

            if msg_id and metadata.get("gmail_message_id") == msg_id:
                return True

            if thread_id and metadata.get("thread_id") == thread_id:
                return True

    except Exception as e:
        log(f"⚠ reply dedupe check failed: {e}", force=True)

    return False


def _update_reply_metrics(lead_id: int, campaign_id: int) -> None:
    now = datetime.utcnow().isoformat()

    try:
        existing = (
            supabase.table("outreach_leads")
            .select("reply_count")
            .eq("id", lead_id)
            .eq("campaign_id", campaign_id)
            .limit(1)
            .execute()
        )

        current_reply_count = 0
        if existing.data:
            current_reply_count = int(existing.data[0].get("reply_count") or 0)

        supabase.table("outreach_leads").update(
            {
                "reply_count": current_reply_count + 1,
                "status": "replied",
                "reply_status": "replied",
                "last_updated": now,
                "last_contacted": now,
            }
        ).eq("id", lead_id).eq("campaign_id", campaign_id).execute()

    except Exception as e:
        log(f"⚠ Failed to update outreach_leads reply count: {e}", force=True)

    try:
        existing = (
            supabase.table("crm_analytics")
            .select("*")
            .eq("lead_id", lead_id)
            .limit(1)
            .execute()
        )

        if existing.data:
            current_replies = int(existing.data[0].get("replies") or 0)
            current_engagement = float(existing.data[0].get("engagement_score") or 0)

            supabase.table("crm_analytics").update(
                {
                    "replies": current_replies + 1,
                    "engagement_score": current_engagement + 5,
                    "last_activity": now,
                }
            ).eq("lead_id", lead_id).execute()
        else:
            supabase.table("crm_analytics").insert(
                {
                    "lead_id": lead_id,
                    "engagement_score": 5,
                    "emails_sent": 0,
                    "opens": 0,
                    "clicks": 0,
                    "replies": 1,
                    "conversions": 0,
                    "last_activity": now,
                }
            ).execute()

    except Exception as e:
        log(f"⚠ Failed to update crm_analytics reply count: {e}", force=True)


def _process_thread_for_lead(service, lead: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    lead_id = lead.get("id")
    campaign_id = lead.get("campaign_id")
    lead_email = _normalize_email(lead.get("email") or "")

    if lead_id is None or campaign_id is None or not lead_email:
        return None

    try:
        thread_ids = _candidate_thread_ids_for_lead(service, lead)
        if not thread_ids:
            return None

        for thread_id in thread_ids:
            try:
                thread = service.users().threads().get(
                    userId="me",
                    id=thread_id,
                    format="full",
                ).execute()
            except Exception as e:
                log(f"⚠ Failed to load thread {thread_id}: {e}", force=True)
                continue

            reply = _thread_has_reply(thread, lead_email)
            if not reply:
                continue

            msg_id = reply.get("gmail_message_id") or ""
            if _reply_already_recorded(int(lead_id), thread_id, msg_id):
                return None

            metadata = {
                "gmail_message_id": msg_id,
                "thread_id": reply.get("thread_id") or thread_id,
                "from": lead_email,
                "subject": reply.get("subject") or "",
                "channel": "gmail",
                "timestamp": reply.get("timestamp") or datetime.utcnow().isoformat(),
                "source": "gmail_api",
            }

            result = store_event(
                lead_id=int(lead_id),
                campaign_id=int(campaign_id),
                event_type="replied",
                metadata=metadata,
            )

            if isinstance(result, dict) and result.get("status") == "duplicate":
                return None

            _mark_processed(msg_id or f"{lead_id}:{thread_id}")

            _update_reply_metrics(int(lead_id), int(campaign_id))

            log(
                f"✅ Reply saved → Lead {lead_id} | Campaign {campaign_id} | Email {lead_email}",
                force=True,
            )

            return {
                "lead_id": str(lead_id),
                "campaign_id": str(campaign_id),
                "sender": lead_email,
                "subject": reply.get("subject") or "",
                "timestamp": reply.get("timestamp") or "",
                "thread_id": thread_id,
                "message_id": msg_id,
            }

    except Exception as e:
        log(f"⚠ Failed to process lead {lead_id}: {e}", force=True)

    return None


def check_for_replies(limit: int = 300) -> List[Dict[str, str]]:
    replies: List[Dict[str, str]] = []

    if not GOOGLE_LIBS_AVAILABLE:
        log("⚠ Gmail reply checking disabled: google libraries missing", force=True)
        return replies

    service = get_service()

    leads = _fetch_candidate_leads(limit=limit)
    if not leads:
        return replies

    for lead in leads:
        try:
            result = _process_thread_for_lead(service, lead)
            if result:
                replies.append(result)
        except Exception as e:
            log(f"⚠ Lead check failed: {e}", force=True)

    return replies


async def start_reply_polling(interval_seconds: int = POLL_INTERVAL_SECONDS) -> None:
    log(f"👂 Reply polling started every {interval_seconds}s", force=True)

    while True:
        try:
            processed = check_for_replies()
            if processed:
                log(f"✅ Replies processed: {len(processed)}", force=True)
        except Exception as e:
            log(f"⚠ Reply polling error: {e}", force=True)

        await asyncio.sleep(interval_seconds)


def _load_watch_mode() -> str:
    return (os.getenv("GMAIL_WATCH_MODE", WATCH_MODE) or "poll").strip().lower()


def start_watch() -> Dict[str, Any]:
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError(
            "Google libraries are not installed. "
            "Switch GMAIL_WATCH_MODE=poll or install Google auth packages."
        )

    creds = _ensure_credentials_valid(_coerce_credentials(_load_credentials_raw()))
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    request_body = {
        "labelIds": ["INBOX"],
        "topicName": _build_topic_name(PROJECT_ID, TOPIC_NAME),
    }

    try:
        response = service.users().watch(userId="me", body=request_body).execute()

        log("👀 Watch started:", response, force=True)

        scopes = set(getattr(creds, "scopes", []) or [])
        if scopes and not (scopes & REQUIRED_WATCH_SCOPES):
            log(
                "⚠ Your token scopes may be too limited for Gmail watch. "
                "If watch fails later, re-auth with gmail.modify.",
                force=True,
            )

        return response

    except HttpError as e:
        content = getattr(e, "content", None)
        if content:
            log(f"❌ Gmail watch failed: {content}", force=True)
        else:
            log(f"❌ Gmail watch failed: {e}", force=True)
        raise

    except Exception as e:
        log(f"❌ Unexpected error while starting Gmail watch: {e}", force=True)
        raise


async def main():
    mode = _load_watch_mode()

    if mode == "watch":
        start_watch()
        return

    await start_reply_polling(interval_seconds=POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())