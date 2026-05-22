from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import traceback
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

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.gmail_auth import authenticate

router = APIRouter(tags=["gmail"])

PROCESS_LOCK = asyncio.Lock()
PROCESSED_MESSAGE_IDS: Dict[str, float] = {}
PROCESSED_MESSAGE_TTL_SECONDS = int(
    os.getenv("GMAIL_PROCESSED_MESSAGE_TTL", "86400")
)

BASE_DIR = Path(__file__).resolve().parents[2]
FALLBACK_HISTORY_FILE = BASE_DIR / "gmail_history_id.txt"

GMAIL_USER_EMAIL = os.getenv("GMAIL_USER_EMAIL", "").strip().lower()
DEBUG_LOGS = (
    os.getenv("GMAIL_DEBUG_LOGS", "false").strip().lower() == "true"
)

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

print("📨 GMAIL WEBHOOK FILE LOADED FROM:", __file__)
print("✅ Gmail webhook routes registered")


def log(*args, force: bool = False) -> None:
    if force or DEBUG_LOGS:
        print(*args)


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


def _mark_processed(message_id: str) -> None:
    if not message_id:
        return

    PROCESSED_MESSAGE_IDS[message_id] = _utc_now().timestamp()


def _was_processed(message_id: str) -> bool:
    if not message_id:
        return False

    return message_id in PROCESSED_MESSAGE_IDS


def _purge_processed_cache() -> None:
    now = _utc_now().timestamp()

    expired = [
        mid
        for mid, ts in PROCESSED_MESSAGE_IDS.items()
        if (now - ts) > PROCESSED_MESSAGE_TTL_SECONDS
    ]

    for mid in expired:
        PROCESSED_MESSAGE_IDS.pop(mid, None)


def get_service():
    if not GOOGLE_LIBS_AVAILABLE or build is None:
        raise RuntimeError("googleapiclient is not installed.")

    creds = authenticate()

    return build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False,
    )


def _load_last_history_id() -> Optional[str]:
    if FALLBACK_HISTORY_FILE.exists():
        try:
            val = FALLBACK_HISTORY_FILE.read_text(
                encoding="utf-8"
            ).strip()

            if val and not val.startswith("{"):
                return val

        except Exception as e:
            log(
                f"⚠ Failed reading fallback history file: {e}",
                force=True,
            )

    return None


def _save_history_id(history_id: str) -> None:
    if not history_id:
        return

    try:
        FALLBACK_HISTORY_FILE.write_text(
            str(history_id).strip(),
            encoding="utf-8",
        )

    except Exception as e:
        log(
            f"⚠ Failed writing history file: {e}",
            force=True,
        )


def is_real_reply(msg: Dict[str, Any]) -> bool:
    headers = msg.get("payload", {}).get("headers", []) or []

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

    if (
        subject.startswith("re:")
        or subject.startswith("fw:")
        or "re:" in subject[:12]
    ):
        return True

    return False


async def _read_json_body(request: Request) -> Dict[str, Any]:
    """
    Bulletproof body parser:
    - handles empty request bodies
    - handles malformed JSON
    - handles form payloads
    - never raises JSON decode errors
    """
    try:
        raw = await request.body()
    except Exception as e:
        log(f"⚠ Failed reading body: {e}", force=True)
        return {}

    if raw is None or len(raw) == 0:
        log("⚠ Empty request body", force=True)
        return {}

    try:
        text = raw.decode("utf-8", errors="ignore").strip()
    except Exception as e:
        log(f"⚠ Failed decoding body: {e}", force=True)
        return {}

    if not text:
        log("⚠ Request body decoded to empty string", force=True)
        return {}

    log(f"📦 RAW BODY TEXT: {text[:1000]}", force=True)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        log("⚠ Parsed JSON is not a dict", force=True)
        return {}
    except json.JSONDecodeError as e:
        log(f"⚠ JSON decode failed: {e}", force=True)
    except Exception as e:
        log(f"⚠ Unexpected JSON parse error: {e}", force=True)

    try:
        form = await request.form()
        if form:
            form_dict = dict(form)
            log(f"📦 FORM BODY: {form_dict}", force=True)
            return form_dict
    except Exception:
        pass

    try:
        padding = "=" * (-len(text) % 4)
        decoded = base64.urlsafe_b64decode((text + padding).encode("utf-8")).decode("utf-8")
        parsed = json.loads(decoded)
        if isinstance(parsed, dict):
            log("✅ Parsed base64 body", force=True)
            return parsed
    except Exception:
        pass

    log("⚠ Could not parse request body", force=True)
    return {}


def _decode_pubsub_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict):
        return {}

    message = body.get("message")
    if not isinstance(message, dict):
        return body

    encoded = message.get("data")
    if not encoded:
        return body

    try:
        encoded_str = encoded.decode("utf-8") if isinstance(encoded, (bytes, bytearray)) else str(encoded)
        padding = "=" * (-len(encoded_str) % 4)
        decoded = base64.urlsafe_b64decode(
            (encoded_str + padding).encode("utf-8")
        ).decode("utf-8")

        parsed = json.loads(decoded)
        return parsed if isinstance(parsed, dict) else {}
    except Exception as e:
        log(
            f"⚠ Failed decoding Pub/Sub payload: {e}",
            force=True,
        )
        return {}


def _safe_insert_lead_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        res = supabase.table("lead_events").insert(payload).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        msg = str(e).lower()
        if (
            "campaign_id" in msg
            or "does not exist" in msg
            or "schema cache" in msg
        ):
            fallback = dict(payload)
            fallback.pop("campaign_id", None)
            res = supabase.table("lead_events").insert(fallback).execute()
            return {"status": "success", "data": res.data}
        raise


def _find_lead(
    sender_email: Optional[str],
) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    blocked_statuses = {"deleted", "archived"}

    if not sender_email:
        return None, None, None

    sender_email = sender_email.strip().lower()

    try:
        res = (
            supabase.table("outreach_leads")
            .select(
                "id, campaign_id, status, email, "
                "last_updated, created_at"
            )
            .ilike("email", sender_email)
            .order("last_updated", desc=True)
            .limit(10)
            .execute()
        )

        if not res.data:
            return None, None, None

        outreach_lead_id = None
        campaign_id = None

        for row in res.data:
            status = (row.get("status") or "").lower()

            if status not in blocked_statuses:
                if row.get("campaign_id") is None:
                    continue

                outreach_lead_id = int(row["id"])
                campaign_id = int(row["campaign_id"])
                break

        if outreach_lead_id is None:
            return None, None, None

    except Exception as e:
        log(
            f"⚠ outreach lead lookup failed: {e}",
            force=True,
        )
        return None, None, None

    system_lead_id = None

    try:
        res = (
            supabase.table("leads")
            .select("id")
            .ilike("email", sender_email)
            .limit(1)
            .execute()
        )

        if res.data:
            system_lead_id = str(res.data[0].get("id") or "")

    except Exception as e:
        log(
            f"⚠ system lead lookup failed: {e}",
            force=True,
        )

    return (
        system_lead_id,
        outreach_lead_id,
        campaign_id,
    )


def _reply_already_recorded(
    lead_id: str,
    msg_id: str,
    thread_id: str = "",
) -> bool:
    try:
        res = (
            supabase.table("lead_events")
            .select("metadata")
            .eq("lead_id", lead_id)
            .eq("event_type", "replied")
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
        log(
            f"⚠ reply dedupe check failed: {e}",
            force=True,
        )

    return False


def _update_reply_metrics(
    outreach_id: int,
    campaign_id: int,
    system_lead_id: Optional[str],
) -> None:
    now = _utc_now_iso()

    try:
        existing = (
            supabase.table("outreach_leads")
            .select("reply_count")
            .eq("id", outreach_id)
            .eq("campaign_id", campaign_id)
            .limit(1)
            .execute()
        )

        current_reply_count = 0

        if existing.data:
            current_reply_count = int(
                existing.data[0].get("reply_count") or 0
            )

        (
            supabase.table("outreach_leads")
            .update(
                {
                    "reply_count": current_reply_count + 1,
                    "status": "replied",
                    "last_updated": now,
                    "next_followup": None,
                }
            )
            .eq("id", outreach_id)
            .eq("campaign_id", campaign_id)
            .execute()
        )

    except Exception as e:
        log(
            f"⚠ Failed updating outreach lead: {e}",
            force=True,
        )

    if not system_lead_id:
        return

    try:
        existing = (
            supabase.table("leads")
            .select("reply_count")
            .eq("id", system_lead_id)
            .limit(1)
            .execute()
        )

        current_reply_count = 0

        if existing.data:
            current_reply_count = int(
                existing.data[0].get("reply_count") or 0
            )

        (
            supabase.table("leads")
            .update(
                {
                    "reply_count": current_reply_count + 1,
                    "reply_status": "replied",
                    "updated_at": now,
                    "pipeline_stage": "Replied",
                }
            )
            .eq("id", system_lead_id)
            .execute()
        )

    except Exception as e:
        log(
            f"⚠ Failed updating leads table: {e}",
            force=True,
        )


def _process_reply_message(service, msg_id: str) -> bool:
    if not msg_id or _was_processed(msg_id):
        return False

    try:
        msg = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=msg_id,
                format="full",
            )
            .execute()
        )

    except HttpError as e:
        if getattr(e.resp, "status", None) == 404:
            _mark_processed(msg_id)
            return False

        log(
            f"⚠ Failed fetching Gmail message: {e}",
            force=True,
        )

        _mark_processed(msg_id)
        return False

    except Exception as e:
        log(
            f"⚠ Failed fetching Gmail message: {e}",
            force=True,
        )

        _mark_processed(msg_id)
        return False

    headers = (
        msg.get("payload", {}).get("headers", [])
        or []
    )

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

    if (
        not sender
        or _is_ignored_sender(sender)
        or (
            GMAIL_USER_EMAIL
            and sender == GMAIL_USER_EMAIL
        )
    ):
        _mark_processed(msg_id)
        return False

    if not is_real_reply(msg):
        _mark_processed(msg_id)
        return False

    (
        system_lead_id,
        outreach_id,
        campaign_id,
    ) = _find_lead(sender)

    if (
        not system_lead_id
        or not outreach_id
        or not campaign_id
    ):
        _mark_processed(msg_id)
        return False

    if _reply_already_recorded(
        system_lead_id,
        msg_id,
        thread_id=thread_id,
    ):
        _mark_processed(msg_id)
        return False

    metadata = {
        "gmail_message_id": msg_id,
        "thread_id": thread_id,
        "from": sender,
        "subject": subject,
        "channel": "gmail",
        "timestamp": _utc_now_iso(),
    }

    payload = {
        "lead_id": system_lead_id,
        "campaign_id": campaign_id,
        "event_type": "replied",
        "metadata": metadata,
        "timestamp": _utc_now_iso(),
    }

    try:
        _safe_insert_lead_event(payload)

        _mark_processed(msg_id)

        _update_reply_metrics(
            outreach_id,
            campaign_id,
            system_lead_id,
        )

        log(
            f"✅ Reply saved → "
            f"Lead {outreach_id} | "
            f"Campaign {campaign_id} | "
            f"From {sender}",
            force=True,
        )

        return True

    except Exception as e:
        _mark_processed(msg_id)

        log(
            f"⚠ Failed storing reply event: {e}",
            force=True,
        )

        return False


@router.get("/ping")
async def gmail_ping():
    return {
        "status": "ok",
        "service": "gmail router alive",
    }


@router.get("/webhook")
async def gmail_webhook_get():
    return {
        "status": "ok",
        "message": "gmail webhook endpoint exists",
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


async def process_gmail_webhook(request: Request):
    log("🔥 POST /gmail/webhook HIT", force=True)

    async with PROCESS_LOCK:
        _purge_processed_cache()

        try:
            body = await _read_json_body(request)

            log(
                f"📦 BODY TYPE: {type(body)}",
                force=True,
            )

            log(
                f"📦 BODY VALUE: {body}",
                force=True,
            )

            if not body:
                return {
                    "status": "ignored",
                    "reason": "empty_body",
                }

            decoded = _decode_pubsub_payload(body)

            event_payload = decoded if decoded else body

            log(
                f"📨 DECODED PAYLOAD: {event_payload}",
                force=True,
            )

            new_history_id = str(
                event_payload.get("historyId")
                or event_payload.get("history_id")
                or ""
            ).strip()

            if not new_history_id:
                return {
                    "status": "ignored",
                    "reason": "missing_history_id",
                }

            service = get_service()

            last_history_id = _load_last_history_id()

            if not last_history_id:
                _save_history_id(new_history_id)

                return {
                    "status": "initialized",
                }

            processed = 0

            try:
                history = (
                    service.users()
                    .history()
                    .list(
                        userId="me",
                        startHistoryId=last_history_id,
                        historyTypes=["messageAdded"],
                    )
                    .execute()
                )

            except HttpError as e:
                if getattr(e.resp, "status", None) == 404:
                    _save_history_id(new_history_id)

                    return {
                        "status": "reset_history",
                    }

                raise

            for h in history.get("history", []):
                for m in h.get("messagesAdded", []):
                    msg_id = (
                        m.get("message", {}).get("id")
                    )

                    if not msg_id:
                        continue

                    try:
                        if _process_reply_message(
                            service,
                            msg_id,
                        ):
                            processed += 1

                    except Exception as e:
                        log(
                            f"⚠ Failed processing "
                            f"{msg_id}: {e}",
                            force=True,
                        )

            _save_history_id(new_history_id)

            return {
                "status": "ok",
                "processed": processed,
            }

        except HttpError as e:
            log(
                f"❌ Gmail HTTP ERROR: {e}",
                force=True,
            )

            return {
                "status": "error",
                "error": str(e),
            }

        except Exception as e:
            traceback.print_exc()

            log(
                f"❌ WEBHOOK ERROR TYPE: {type(e)}",
                force=True,
            )

            log(
                f"❌ WEBHOOK ERROR: {repr(e)}",
                force=True,
            )

            return {
                "status": "error",
                "error": repr(e),
            }
