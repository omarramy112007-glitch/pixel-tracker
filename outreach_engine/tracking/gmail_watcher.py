# outreach_engine/tracking/gmail_watcher.py

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
    Credentials = None
    build = None
    HttpError = Exception
    GOOGLE_LIBS_AVAILABLE = False

from outreach_engine.database.supabase_client import (
    get_lead_by_email,
    get_outreach_lead_by_email_campaign,
    insert_event,
    record_reply,
    supabase,
)
from outreach_engine.tracking.gmail_auth import authenticate
from outreach_engine.processors.follow_up_manager import mark_lead_replied
from outreach_engine.core.account_manager import get_active_accounts


# ---------------------------------------------------------------------------
# ENV
# ---------------------------------------------------------------------------

PROJECT_ID           = os.getenv("GMAIL_PROJECT_ID",          "make-487214").strip()
TOPIC_NAME           = os.getenv("GMAIL_PUBSUB_TOPIC",         "gmail-replies").strip()
ROOT_DIR             = Path(__file__).resolve().parents[2]
TOKEN_JSON_PATH      = Path(os.getenv("GMAIL_TOKEN_JSON_PATH", str(ROOT_DIR / "token.json")))
WATCH_MODE           = os.getenv("GMAIL_WATCH_MODE",           "poll").strip().lower()
POLL_INTERVAL_SECONDS= int(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "60"))
GMAIL_USER_EMAIL     = os.getenv("GMAIL_USER_EMAIL",           "").strip().lower()
DEBUG_LOGS           = os.getenv("GMAIL_DEBUG_LOGS",           "false").strip().lower() == "true"
MAX_THREAD_LOOKUPS_PER_LEAD = int(os.getenv("GMAIL_MAX_THREAD_LOOKUPS", "5"))
THREAD_LOOKBACK_DAYS        = int(os.getenv("GMAIL_THREAD_LOOKBACK_DAYS", "30"))

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

PROCESS_LOCK                  = asyncio.Lock()
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

# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------

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


def _normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def _is_ignored_sender(sender: str) -> bool:
    sender = (sender or "").strip().lower()
    if not sender:
        return True
    if sender.startswith(IGNORED_PREFIXES):
        return True
    domain = sender.split("@")[-1] if "@" in sender else ""
    return domain in IGNORED_DOMAINS


# ---------------------------------------------------------------------------
# Reply counter + finalizer
# ---------------------------------------------------------------------------

def _increment_reply_count_and_finalize(
    outreach_lead_id: int,
    campaign_id: int,
    lead_email: str,
) -> None:
    now = utc_now_iso()

    # 1. Increment reply_count
    try:
        res = (
            supabase.table("outreach_leads")
            .select("reply_count")
            .eq("id", outreach_lead_id)
            .limit(1)
            .execute()
        )
        current = 0
        if res.data:
            try:
                current = int(res.data[0].get("reply_count") or 0)
            except Exception:
                current = 0

        supabase.table("outreach_leads").update({
            "reply_count":  current + 1,
            "replied_at":   now,
            "last_updated": now,
        }).eq("id", outreach_lead_id).execute()

        log(
            f"📈 reply_count++ → outreach_lead={outreach_lead_id} "
            f"({current} → {current + 1})",
            force=True,
        )
    except Exception as e:
        log(f"⚠ reply_count increment failed for lead {outreach_lead_id}: {e}", force=True)

    # 2. Terminal state — stops follow-up loop
    try:
        mark_lead_replied(lead_email, int(campaign_id))
    except Exception as e:
        log(f"⚠ mark_lead_replied failed for {lead_email}: {e}", force=True)

    # 3. Sync crm_analytics.replies
    try:
        system_lead = get_lead_by_email(lead_email) if lead_email else None
        system_lead_id = (
            str(system_lead["id"])
            if system_lead and system_lead.get("id")
            else None
        )
        if system_lead_id:
            _sync_crm_reply(system_lead_id)
    except Exception as e:
        log(f"⚠ crm_analytics reply sync failed for {lead_email}: {e}", force=True)


def _sync_crm_reply(system_lead_id: str) -> None:
    now = utc_now_iso()
    try:
        res = (
            supabase.table("crm_analytics")
            .select("emails_sent, opens, clicks, replies, conversions, engagement_score")
            .eq("lead_id", system_lead_id)
            .limit(1)
            .execute()
        )
        row = res.data[0] if res.data else {}

        def _i(v):
            try:
                return int(v or 0)
            except Exception:
                return 0

        emails_sent = _i(row.get("emails_sent"))
        opens       = _i(row.get("opens"))
        clicks      = _i(row.get("clicks"))
        replies     = _i(row.get("replies")) + 1
        conversions = _i(row.get("conversions"))

        engagement_score = (
            emails_sent * 1 + opens * 2 + clicks * 3
            + replies * 5 + conversions * 10
        )

        supabase.table("crm_analytics").upsert({
            "lead_id":          system_lead_id,
            "emails_sent":      emails_sent,
            "opens":            opens,
            "clicks":           clicks,
            "replies":          replies,
            "conversions":      conversions,
            "engagement_score": engagement_score,
            "last_activity":    now,
        }).execute()
        log(f"📊 crm_analytics.replies++ → system_lead={system_lead_id}", force=True)
    except Exception as e:
        log(f"⚠ crm_analytics reply upsert failed: {e}", force=True)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

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


def get_service_for_account(decoded_token: Dict[str, Any]):
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError("Google libraries are not installed.")
    creds = _ensure_credentials_valid(_coerce_credentials(decoded_token))
    return build("gmail", "v1", credentials=creds, cache_discovery=False)

# ---------------------------------------------------------------------------
# History ID
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Lead lookup
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = {
    "converted", "won", "lost", "closed",
    "archived", "deleted", "completed",
    "unsubscribed", "opt-out",
}

ACTIVE_STATUSES = {
    "pending", "new", "not_contacted", "sent",
    "followup_no_open", "followup_soft_open",
    "interested_followup", "contacted",
    "replied",
}


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


def _lead_is_eligible(lead: Dict[str, Any]) -> bool:
    status          = str(lead.get("status")          or "").strip().lower()
    followup_status = str(lead.get("followup_status") or "").strip().lower()
    email           = _normalize_email(lead.get("email") or "")

    if not email:
        return False
    if _is_ignored_sender(email):
        return False
    if status in TERMINAL_STATUSES:
        return False

    return bool(
        lead.get("last_email_sent")
        or status in {
            "sent", "followup_no_open", "followup_soft_open",
            "interested_followup", "replied",
        }
        or followup_status in {"no_open", "soft_open"}
    )


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
    """
    Search Gmail for threads from this lead's email address.

    FIX: Uses last_email_sent time + email to build a precise search
    instead of relying on stored thread_id.

    Search strategy:
      1. If last_email_sent exists → search from that date onward
      2. Fall back to THREAD_LOOKBACK_DAYS
    """
    email = _normalize_email(lead.get("email") or "")
    if not email:
        return []

    # Build time-based search using last_email_sent
    last_sent = lead.get("last_email_sent")
    if last_sent:
        try:
            dt = datetime.fromisoformat(str(last_sent).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            # Gmail "after:" uses YYYY/MM/DD format
            after_date = dt.strftime("%Y/%m/%d")
            query = (
                f"from:{email} after:{after_date} "
                f"-category:promotions -label:spam"
            )
        except Exception:
            # Fallback to newer_than if date parsing fails
            query = (
                f"from:{email} newer_than:{THREAD_LOOKBACK_DAYS}d "
                f"-category:promotions -label:spam"
            )
    else:
        # No last_email_sent — use day-based fallback
        query = (
            f"from:{email} newer_than:{THREAD_LOOKBACK_DAYS}d "
            f"-category:promotions -label:spam"
        )

    log(f"🔍 Gmail search → {query}", force=True)

    try:
        res = service.users().threads().list(
            userId="me",
            q=query,
            maxResults=MAX_THREAD_LOOKUPS_PER_LEAD,
        ).execute()
        return [t.get("id") for t in (res.get("threads") or []) if t.get("id")]
    except Exception as e:
        log(f"⚠ Thread search failed for {email}: {e}", force=True)
        return []


def _message_sender(msg: Dict[str, Any]) -> str:
    headers  = msg.get("payload", {}).get("headers", []) or []
    from_raw = next(
        (x.get("value") for x in headers if (x.get("name") or "").lower() == "from"),
        "",
    )
    return _extract_email(from_raw)


def _message_subject(msg: Dict[str, Any]) -> str:
    headers = msg.get("payload", {}).get("headers", []) or []
    return next(
        (x.get("value") for x in headers if (x.get("name") or "").lower() == "subject"),
        "",
    )


def _is_reply_headers(headers: List[Dict[str, Any]]) -> bool:
    subject     = ""
    in_reply_to = False
    references  = False
    for h in headers:
        name = (h.get("name") or "").strip().lower()
        val  = h.get("value") or ""
        if name == "subject":
            subject = val.lower().strip()
        elif name == "in-reply-to":
            in_reply_to = True
        elif name == "references":
            references = True
    if in_reply_to or references:
        return True
    return subject.startswith(("re:", "fw:"))


def _already_recorded_msg_ids(system_lead_id: str) -> set:
    recorded = set()
    if not system_lead_id:
        return recorded
    try:
        res = (
            supabase.table("lead_events")
            .select("metadata")
            .eq("lead_id", system_lead_id)
            .eq("event_type", "replied")
            .limit(500)
            .execute()
        )
        for row in res.data or []:
            meta = row.get("metadata") or {}
            if not isinstance(meta, dict):
                continue
            mid = meta.get("gmail_message_id")
            if mid:
                recorded.add(mid)
    except Exception as e:
        log(f"⚠ Reply dedupe fetch failed: {e}", force=True)
    return recorded


def _collect_all_replies(
    thread: Dict[str, Any],
    lead_email: str,
) -> List[Dict[str, Any]]:
    messages   = thread.get("messages", []) or []
    lead_email = _normalize_email(lead_email)
    replies    = []

    if len(messages) < 2:
        return replies

    ordered = sorted(messages, key=lambda m: int(m.get("internalDate") or 0))

    for idx, msg in enumerate(ordered):
        sender = _message_sender(msg)
        if not sender:
            continue
        if sender != lead_email:
            continue
        if _is_ignored_sender(sender):
            continue
        if GMAIL_USER_EMAIL and sender == GMAIL_USER_EMAIL:
            continue
        if idx == 0:
            continue

        headers = msg.get("payload", {}).get("headers", []) or []
        if not _is_reply_headers(headers):
            continue

        msg_id = msg.get("id") or ""
        replies.append({
            "gmail_message_id": msg_id,
            "thread_id":        thread.get("id") or msg.get("threadId") or "",
            "from":             sender,
            "subject":          _message_subject(msg),
            "timestamp":        utc_now_iso(),
            "internal_date":    int(msg.get("internalDate") or 0),
        })

    return replies


def _process_thread_for_lead(service, lead: Dict[str, Any]) -> List[Dict[str, Any]]:
    outreach_id = lead.get("id")
    campaign_id = lead.get("campaign_id")
    lead_email  = _normalize_email(lead.get("email") or "")

    if outreach_id is None or campaign_id is None or not lead_email:
        return []

    system_lead_id = _lookup_system_lead_id(lead_email)
    already_recorded = _already_recorded_msg_ids(
        str(system_lead_id) if system_lead_id else ""
    )

    results = []

    try:
        thread_ids = _candidate_thread_ids_for_lead(service, lead)
        if not thread_ids:
            return []

        for thread_id in thread_ids:
            try:
                thread = service.users().threads().get(
                    userId="me", id=thread_id, format="full"
                ).execute()
            except Exception as e:
                log(f"⚠ Failed to load thread {thread_id}: {e}", force=True)
                continue

            all_replies = _collect_all_replies(thread, lead_email)
            if not all_replies:
                continue

            for reply in all_replies:
                msg_id = reply.get("gmail_message_id") or ""

                if _was_processed(msg_id):
                    continue

                if msg_id and msg_id in already_recorded:
                    _mark_processed(msg_id)
                    continue

                metadata = {
                    "gmail_message_id":     msg_id,
                    "thread_id":            reply.get("thread_id") or thread_id,
                    "from":                 lead_email,
                    "subject":              reply.get("subject") or "",
                    "channel":              "gmail",
                    "timestamp":            reply.get("timestamp") or utc_now_iso(),
                    "source":               "gmail_api",
                    "campaign_id":          int(campaign_id),
                    "lead_followup_status": str(lead.get("followup_status") or ""),
                    "lead_status_at_reply": str(lead.get("status") or ""),
                }

                record_reply(
                    lead_id=int(outreach_id),
                    campaign_id=int(campaign_id),
                    email=lead_email,
                    metadata=metadata,
                )

                if system_lead_id:
                    insert_event({
                        "lead_id":    system_lead_id,
                        "event_type": "replied",
                        "metadata":   metadata,
                    })

                _increment_reply_count_and_finalize(
                    outreach_lead_id=int(outreach_id),
                    campaign_id=int(campaign_id),
                    lead_email=lead_email,
                )

                already_recorded.add(msg_id)
                _mark_processed(
                    msg_id
                    or f"{outreach_id}:{thread_id}:{reply['internal_date']}"
                )

                log(
                    f"✅ Reply saved → Lead {outreach_id} | "
                    f"Campaign {campaign_id} | Email {lead_email} | "
                    f"msg={msg_id} | "
                    f"followup_status={lead.get('followup_status')}",
                    force=True,
                )

                results.append({
                    "lead_id":        str(outreach_id),
                    "campaign_id":    str(campaign_id),
                    "sender":         lead_email,
                    "subject":        reply.get("subject") or "",
                    "timestamp":      reply.get("timestamp") or "",
                    "thread_id":      thread_id,
                    "message_id":     msg_id,
                    "followup_status": str(lead.get("followup_status") or ""),
                })

    except Exception as e:
        log(f"⚠ Failed to process lead {outreach_id}: {e}", force=True)

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_for_replies(limit: int = 300) -> List[Dict[str, str]]:
    """
    Single, correct version — checks replies across ALL active sending
    accounts, filtering each account's candidate leads to only the ones
    that were sent mail FROM that specific account.
    """
    if not GOOGLE_LIBS_AVAILABLE:
        log("⚠ Gmail reply checking disabled: google libraries missing", force=True)
        return []

    accounts = get_active_accounts()
    if not accounts:
        # Backward-compat: no accounts registered yet, fall back to the
        # single legacy token-based account
        try:
            creds   = _ensure_credentials_valid(_coerce_credentials(_load_credentials_raw()))
            service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            accounts = [{"account_key": None, "_service": service}]
        except Exception as e:
            log(f"❌ Cannot get Gmail service: {e}", force=True)
            return []

    all_candidates = _fetch_candidate_leads(limit=limit)
    results = []

    for account in accounts:
        account_key = account.get("account_key")

        if account.get("_decoded_token") is None:
            log(f"⚠ Skipping account {account_key} — token failed to decode", force=True)
            continue

        try:
            service = account.get("_service") or get_service_for_account(
                account["_decoded_token"]
            )
        except Exception as e:
            log(f"❌ Cannot get service for account {account_key}: {e}", force=True)
            continue

        leads_for_this_account = [
            l for l in all_candidates
            if l.get("sending_account") == account_key
        ]

        for lead in leads_for_this_account:
            try:
                reply_list = _process_thread_for_lead(service, lead)
                results.extend(reply_list)
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


def _build_topic_name(project_id: str, topic_name: str) -> str:
    if not topic_name:
        raise ValueError("GMAIL_PUBSUB_TOPIC is empty")
    if topic_name.startswith("projects/"):
        return topic_name
    if not project_id:
        raise ValueError("GMAIL_PROJECT_ID is empty")
    return f"projects/{project_id}/topics/{topic_name}"


def start_watch() -> Dict[str, Any]:
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError("Google libraries are not installed.")
    creds   = _ensure_credentials_valid(_coerce_credentials(_load_credentials_raw()))
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    try:
        response = service.users().watch(
            userId="me",
            body={
                "labelIds": ["INBOX"],
                "topicName": _build_topic_name(PROJECT_ID, TOPIC_NAME),
            },
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
