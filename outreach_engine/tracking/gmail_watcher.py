from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from datetime import datetime, timezone
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
    Credentials       = None
    build             = None
    HttpError         = Exception
    GOOGLE_LIBS_AVAILABLE = False

from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.gmail_auth import authenticate

# ── ENV ───────────────────────────────────────────────────────────────────────

PROJECT_ID  = os.getenv("GMAIL_PROJECT_ID", "make-487214").strip()
TOPIC_NAME  = os.getenv("GMAIL_PUBSUB_TOPIC", "gmail-replies").strip()

ROOT_DIR        = Path(__file__).resolve().parents[2]
TOKEN_JSON_PATH = Path(os.getenv("GMAIL_TOKEN_JSON_PATH", str(ROOT_DIR / "token.json")))

WATCH_MODE              = os.getenv("GMAIL_WATCH_MODE", "poll").strip().lower()
POLL_INTERVAL_SECONDS   = int(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "60"))
GMAIL_USER_EMAIL        = os.getenv("GMAIL_USER_EMAIL", "").strip().lower()
DEBUG_LOGS              = os.getenv("GMAIL_DEBUG_LOGS", "false").strip().lower() == "true"

MAX_THREAD_LOOKUPS_PER_LEAD = int(os.getenv("GMAIL_MAX_THREAD_LOOKUPS", "5"))
THREAD_LOOKBACK_DAYS        = int(os.getenv("GMAIL_THREAD_LOOKBACK_DAYS", "30"))

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

REQUIRED_WATCH_SCOPES = {
    "https://www.googleapis.com/auth/gmail.modify",
}

PROCESS_LOCK = asyncio.Lock()
PROCESSED_MESSAGE_IDS: Dict[str, float] = {}
PROCESSED_MESSAGE_TTL_SECONDS = int(os.getenv("GMAIL_PROCESSED_MESSAGE_TTL", "86400"))

IGNORED_DOMAINS = {
    "notify.railway.app", "github.com", "redditmail.com",
    "discover.pinterest.com", "pinterest.com", "quora.com",
    "coursera.org", "coursera.com", "apollo.io",
    "stockanalysis.com", "talabat.com", "mail.theresanaiforthat.com",
}

IGNORED_PREFIXES = (
    "noreply@", "no-reply@", "donotreply@",
    "do-not-reply@", "hello@notify.",
)

HISTORY_ID_KEY = "gmail_history_id"

# ── Utils ─────────────────────────────────────────────────────────────────────

def log(*args, force: bool = False) -> None:
    if force or DEBUG_LOGS:
        print(*args)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> float:
    return time.time()


def _purge_processed_cache() -> None:
    now     = _now_ts()
    expired = [mid for mid, ts in PROCESSED_MESSAGE_IDS.items()
               if (now - ts) > PROCESSED_MESSAGE_TTL_SECONDS]
    for mid in expired:
        PROCESSED_MESSAGE_IDS.pop(mid, None)


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
    raw   = value.strip().lower()
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

# ── Credentials ───────────────────────────────────────────────────────────────

def _load_credentials_from_b64_env():
    token_b64 = os.getenv("GMAIL_TOKEN_B64", "").strip()
    if not token_b64:
        return None
    try:
        token_info = json.loads(base64.b64decode(token_b64).decode("utf-8"))
        if not isinstance(token_info, dict):
            raise ValueError("Not a JSON object")
        if not GOOGLE_LIBS_AVAILABLE or Credentials is None:
            raise RuntimeError("google libraries missing")
        creds = Credentials.from_authorized_user_info(token_info, scopes=SCOPES)
        log("✅ Using GMAIL_TOKEN_B64 credentials", force=True)
        return creds
    except Exception as e:
        log(f"⚠ Failed to load GMAIL_TOKEN_B64: {e}", force=True)
        return None


def _load_credentials_from_file():
    if not GOOGLE_LIBS_AVAILABLE or Credentials is None:
        return None
    if not TOKEN_JSON_PATH.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_JSON_PATH), SCOPES)
        log(f"📄 Loaded token.json from: {TOKEN_JSON_PATH}", force=True)
        return creds
    except Exception as e:
        log(f"⚠ Failed to load token.json: {e}", force=True)
        return None


def _load_credentials_raw():
    return (
        _load_credentials_from_b64_env()
        or _load_credentials_from_file()
        or authenticate()
    )


def _coerce_credentials(raw):
    if not GOOGLE_LIBS_AVAILABLE or Credentials is None:
        raise RuntimeError("Google libraries are not installed.")
    if isinstance(raw, Credentials):
        return raw
    if isinstance(raw, dict):
        return Credentials.from_authorized_user_info(raw, scopes=SCOPES)
    raise TypeError(f"Unsupported credentials type: {type(raw)!r}")


def _save_credentials(creds) -> None:
    try:
        if hasattr(creds, "to_json"):
            TOKEN_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_JSON_PATH.write_text(creds.to_json(), encoding="utf-8")
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
                raise RuntimeError("GoogleAuthRequest unavailable")
            creds.refresh(GoogleAuthRequest())
            _save_credentials(creds)
            log("✅ Gmail credentials refreshed.", force=True)
        except Exception as e:
            log(f"❌ Gmail token refresh FAILED: {e}", force=True)
            raise RuntimeError(f"Failed to refresh Gmail credentials: {e}") from e

    if not getattr(creds, "valid", True):
        raise RuntimeError("Gmail credentials are invalid.")

    return creds


def get_service():
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError("Google libraries are not installed.")
    raw   = _load_credentials_raw()
    creds = _ensure_credentials_valid(_coerce_credentials(raw))
    _save_credentials(creds)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)

# ── History ID ────────────────────────────────────────────────────────────────

def _load_last_history_id() -> Optional[str]:
    try:
        res = (
            supabase.table("system_config")
            .select("value")
            .eq("key", HISTORY_ID_KEY)
            .limit(1)
            .execute()
        )
        if res.data:
            val = str(res.data[0]["value"]).strip()
            if val:
                return val
    except Exception as e:
        log(f"⚠ Could not load history_id: {e}", force=True)
    return None


def _save_history_id(history_id: str) -> None:
    if not history_id:
        return
    try:
        supabase.table("system_config").upsert(
            {"key": HISTORY_ID_KEY, "value": str(history_id).strip()},
            on_conflict="key",
        ).execute()
    except Exception as e:
        log(f"⚠ Could not save history_id: {e}", force=True)

# ── Reply detection ───────────────────────────────────────────────────────────

def _build_topic_name(project_id: str, topic_name: str) -> str:
    if not topic_name:
        raise ValueError("GMAIL_PUBSUB_TOPIC is empty")
    if topic_name.startswith("projects/"):
        return topic_name
    if not project_id:
        raise ValueError("GMAIL_PROJECT_ID is empty")
    return f"projects/{project_id}/topics/{topic_name}"


def _is_reply_headers(headers: List[Dict[str, Any]]) -> bool:
    subject = ""
    in_reply_to = references = False
    for h in headers:
        name = (h.get("name") or "").strip().lower()
        val  = h.get("value") or ""
        if name == "subject":       subject     = val.lower().strip()
        elif name == "in-reply-to": in_reply_to = True
        elif name == "references":  references  = True
    if in_reply_to or references:
        return True
    return subject.startswith(("re:", "fw:"))


def _message_sender(msg: Dict[str, Any]) -> str:
    headers  = msg.get("payload", {}).get("headers", []) or []
    from_raw = next((x.get("value") for x in headers if (x.get("name") or "").lower() == "from"), "")
    return _extract_email(from_raw)


def _message_subject(msg: Dict[str, Any]) -> str:
    headers = msg.get("payload", {}).get("headers", []) or []
    return next((x.get("value") for x in headers if (x.get("name") or "").lower() == "subject"), "")


def _lookup_system_lead_id(lead_email: str) -> Optional[str]:
    if not lead_email:
        return None
    try:
        res = (
            supabase.table("leads")
            .select("id,email")
            .ilike("email", lead_email)
            .limit(1)
            .execute()
        )
        if res.data:
            return str(res.data[0].get("id") or "")
    except Exception as e:
        log(f"⚠ System lead lookup failed for {lead_email}: {e}", force=True)
    return None


def _thread_has_reply(thread: Dict[str, Any], lead_email: str) -> Optional[Dict[str, Any]]:
    messages   = thread.get("messages", []) or []
    if len(messages) < 2:
        return None
    ordered    = sorted(messages, key=lambda m: int(m.get("internalDate") or 0))
    lead_email = _normalize_email(lead_email)

    for idx, msg in enumerate(ordered):
        sender = _message_sender(msg)
        if not sender or sender != lead_email:
            continue
        if _is_ignored_sender(sender):
            continue
        if GMAIL_USER_EMAIL and sender == GMAIL_USER_EMAIL:
            continue
        if idx == 0:
            continue
        msg_id  = msg.get("id") or ""
        if _was_processed(msg_id):
            continue
        headers = msg.get("payload", {}).get("headers", []) or []
        if not _is_reply_headers(headers):
            continue
        return {
            "gmail_message_id": msg_id,
            "thread_id":        thread.get("id") or msg.get("threadId") or "",
            "from":             sender,
            "subject":          _message_subject(msg),
            "timestamp":        utc_now_iso(),
        }
    return None


def _lead_is_eligible(lead: Dict[str, Any]) -> bool:
    status       = str(lead.get("status") or "").strip().lower()
    email        = _normalize_email(lead.get("email") or "")
    reply_status = lead.get("reply_status")

    if not email:
        return False
    if _is_ignored_sender(email):
        return False
    if status in {"converted", "won", "lost", "closed", "archived", "deleted", "replied"}:
        return False
    if reply_status in [True, 1, "1", "true", "replied"]:
        return False
    return bool(lead.get("last_email_sent") or status in {"sent", "pending", "opened"})


def _fetch_candidate_leads(limit: int = 300) -> List[Dict[str, Any]]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .neq("email", "")
            .order("last_updated", desc=True)
            .limit(limit)
            .execute()
        )
        return [l for l in (res.data or []) if _lead_is_eligible(l)]
    except Exception as e:
        log(f"⚠ Failed to fetch candidate leads: {e}", force=True)
        return []


def _candidate_thread_ids_for_lead(service, lead: Dict[str, Any]) -> List[str]:
    email = _normalize_email(lead.get("email") or "")
    if not email:
        return []
    stored = (lead.get("thread_id") or "").strip()
    if stored:
        return [stored]
    try:
        res = service.users().threads().list(
            userId="me",
            q=f"from:{email} newer_than:{THREAD_LOOKBACK_DAYS}d -category:promotions -label:spam",
            maxResults=MAX_THREAD_LOOKUPS_PER_LEAD,
        ).execute()
        return [t.get("id") for t in (res.get("threads") or []) if t.get("id")]
    except Exception as e:
        log(f"⚠ Thread search failed for {email}: {e}", force=True)
        return []


def _reply_already_recorded(lead_id: str, thread_id: str, msg_id: str) -> bool:
    try:
        res = (
            supabase.table("lead_events")
            .select("id,metadata")
            .eq("lead_id", lead_id)
            .eq("event_type", "replied")
            .limit(200)
            .execute()
        )
        for row in res.data or []:
            meta = row.get("metadata") or {}
            if not isinstance(meta, dict):
                continue
            if msg_id    and meta.get("gmail_message_id") == msg_id:    return True
            if thread_id and meta.get("thread_id")        == thread_id: return True
    except Exception as e:
        log(f"⚠ Reply dedupe check failed: {e}", force=True)
    return False


def _update_reply_metrics(outreach_id: int, campaign_id: int, system_lead_id: Optional[str]) -> None:
    now = utc_now_iso()
    try:
        existing = (
            supabase.table("outreach_leads")
            .select("reply_count")
            .eq("id", outreach_id)
            .eq("campaign_id", campaign_id)
            .limit(1)
            .execute()
        )
        current = int((existing.data or [{}])[0].get("reply_count") or 0)
        supabase.table("outreach_leads").update({
            "reply_count":   current + 1,
            "status":        "replied",
            "reply_status":  True,          # boolean — not string
            "last_updated":  now,
            "last_contacted": now,
        }).eq("id", outreach_id).eq("campaign_id", campaign_id).execute()
    except Exception as e:
        log(f"⚠ Failed to update outreach_leads metrics: {e}", force=True)

    if not system_lead_id:
        return
    try:
        existing = (
            supabase.table("crm_analytics")
            .select("*")
            .eq("lead_id", system_lead_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            supabase.table("crm_analytics").update({
                "replies":          int(existing.data[0].get("replies") or 0) + 1,
                "engagement_score": float(existing.data[0].get("engagement_score") or 0) + 5,
                "last_activity":    now,
            }).eq("lead_id", system_lead_id).execute()
        else:
            supabase.table("crm_analytics").insert({
                "lead_id": system_lead_id, "engagement_score": 5,
                "emails_sent": 0, "opens": 0, "clicks": 0,
                "replies": 1, "conversions": 0, "last_activity": now,
            }).execute()
    except Exception as e:
        log(f"⚠ Failed to update crm_analytics: {e}", force=True)


def _process_thread_for_lead(service, lead: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    outreach_id    = lead.get("id")
    campaign_id    = lead.get("campaign_id")
    lead_email     = _normalize_email(lead.get("email") or "")

    if outreach_id is None or campaign_id is None or not lead_email:
        return None

    system_lead_id = _lookup_system_lead_id(lead_email)

    try:
        thread_ids = _candidate_thread_ids_for_lead(service, lead)
        if not thread_ids:
            return None

        for thread_id in thread_ids:
            try:
                thread = service.users().threads().get(
                    userId="me", id=thread_id, format="full"
                ).execute()
            except Exception as e:
                log(f"⚠ Failed to load thread {thread_id}: {e}", force=True)
                continue

            reply = _thread_has_reply(thread, lead_email)
            if not reply:
                continue

            msg_id = reply.get("gmail_message_id") or ""
            if _was_processed(msg_id):
                continue
            if system_lead_id and _reply_already_recorded(str(system_lead_id), thread_id, msg_id):
                _mark_processed(msg_id)
                continue

            metadata = {
                "gmail_message_id": msg_id,
                "thread_id":        reply.get("thread_id") or thread_id,
                "from":             lead_email,
                "subject":          reply.get("subject") or "",
                "channel":          "gmail",
                "timestamp":        reply.get("timestamp") or utc_now_iso(),
                "source":           "gmail_api",
            }

            if system_lead_id:
                result = store_event(
                    lead_id=system_lead_id,
                    campaign_id=int(campaign_id),
                    event_type="replied",
                    metadata=metadata,
                )
                if isinstance(result, dict) and result.get("status") == "duplicate":
                    _mark_processed(msg_id)
                    continue

            _mark_processed(msg_id or f"{outreach_id}:{thread_id}")
            _update_reply_metrics(int(outreach_id), int(campaign_id), system_lead_id)

            log(f"✅ Reply saved → Lead {outreach_id} | Campaign {campaign_id} | Email {lead_email}", force=True)

            return {
                "lead_id":    str(outreach_id),
                "campaign_id": str(campaign_id),
                "sender":     lead_email,
                "subject":    reply.get("subject") or "",
                "timestamp":  reply.get("timestamp") or "",
                "thread_id":  thread_id,
                "message_id": msg_id,
            }

    except Exception as e:
        log(f"⚠ Failed to process lead {outreach_id}: {e}", force=True)

    return None

# ── Public API ────────────────────────────────────────────────────────────────

def check_for_replies(limit: int = 300) -> List[Dict[str, str]]:
    if not GOOGLE_LIBS_AVAILABLE:
        log("⚠ Gmail reply checking disabled: google libraries missing", force=True)
        return []
    try:
        service = get_service()
    except Exception as e:
        log(f"❌ Cannot get Gmail service: {e}", force=True)
        return []

    leads   = _fetch_candidate_leads(limit=limit)
    results = []
    for lead in leads:
        try:
            result = _process_thread_for_lead(service, lead)
            if result:
                results.append(result)
        except Exception as e:
            log(f"⚠ Lead check failed: {e}", force=True)
    return results


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


def start_watch() -> Dict[str, Any]:
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError("Google libraries are not installed.")
    creds   = _ensure_credentials_valid(_coerce_credentials(_load_credentials_raw()))
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    try:
        response = service.users().watch(
            userId="me",
            body={"labelIds": ["INBOX"], "topicName": _build_topic_name(PROJECT_ID, TOPIC_NAME)},
        ).execute()
        log(f"👀 Watch started: {response}", force=True)
        return response
    except HttpError as e:
        log(f"❌ Gmail watch failed: {getattr(e, 'content', None) or e}", force=True)
        raise
    except Exception as e:
        log(f"❌ Unexpected error starting Gmail watch: {e}", force=True)
        raise


async def main():
    mode = (os.getenv("GMAIL_WATCH_MODE", WATCH_MODE) or "poll").strip().lower()
    if mode == "watch":
        start_watch()
        return
    await start_reply_polling(interval_seconds=POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
