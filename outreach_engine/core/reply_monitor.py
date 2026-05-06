# outreach_engine/core/reply_monitor.py

from __future__ import annotations

import asyncio
import imaplib
import os
import re
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from email.utils import parseaddr
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, Request

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import store_event

# -----------------------------
# IMAP Config
# -----------------------------
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_USER = os.getenv("IMAP_USER", "").strip()
IMAP_PASS = os.getenv("IMAP_PASS", "").strip()
MAILBOX = os.getenv("IMAP_MAILBOX", "INBOX")
POLL_INTERVAL_SECONDS = int(os.getenv("REPLY_POLL_INTERVAL_SECONDS", "30"))

app = FastAPI(title="Outreach Engine Reply Monitor")

# Prevent processing the same mail twice within this process
_PROCESSED_REPLY_KEYS: set[str] = set()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_campaign_id(lead_id: int) -> Optional[int]:
    """
    Resolve campaign_id from outreach_leads using lead_id.
    """
    try:
        res = (
            supabase.table("outreach_leads")
            .select("campaign_id")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        if res.data:
            campaign_id = res.data[0].get("campaign_id")
            return int(campaign_id) if campaign_id is not None else None
    except Exception as e:
        print(f"⚠ Failed to resolve campaign_id for reply tracking: {e}")
    return None


def _resolve_lead_and_campaign_from_sender(
    sender_header: Optional[str],
) -> Tuple[Optional[int], Optional[int]]:
    """
    Fallback if subject doesn't include [lead:123].
    Tries to resolve the lead by sender email address.
    """
    if not sender_header:
        return None, None

    sender_email = parseaddr(sender_header)[1].strip().lower()
    if not sender_email:
        return None, None

    try:
        res = (
            supabase.table("outreach_leads")
            .select("id, campaign_id, email, created_at")
            .eq("email", sender_email)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            row = res.data[0]
            lead_id = row.get("id")
            campaign_id = row.get("campaign_id")
            return (
                int(lead_id) if lead_id is not None else None,
                int(campaign_id) if campaign_id is not None else None,
            )
    except Exception as e:
        print(f"⚠ Failed to resolve lead from sender: {e}")

    return None, None


def _decode_subject(raw_subject: Optional[str]) -> str:
    if not raw_subject:
        return ""

    parts = decode_header(raw_subject)
    out = []
    for value, encoding in parts:
        if isinstance(value, bytes):
            out.append(value.decode(encoding or "utf-8", errors="ignore"))
        else:
            out.append(str(value))
    return "".join(out).strip()


def _reply_key(
    lead_id: Optional[int],
    campaign_id: Optional[int],
    subject: str,
    sender: Optional[str],
    message_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> str:
    """
    Stable key to avoid double-processing the same reply.
    Prefer the real Message-ID first.
    """
    if message_id:
        return f"msg:{message_id.strip().lower()}"

    sender_norm = (parseaddr(sender or "")[1] or "").strip().lower()
    return f"{lead_id}:{campaign_id}:{sender_norm}:{subject.strip().lower()}:{timestamp or ''}"


def _update_reply_metrics(lead_id: int, campaign_id: int) -> None:
    """
    Update outreach_leads + crm_analytics after a reply is received.
    """
    now = _utc_now_iso()

    try:
        row = (
            supabase.table("outreach_leads")
            .select("reply_count")
            .eq("id", lead_id)
            .eq("campaign_id", campaign_id)
            .limit(1)
            .execute()
        )

        current_reply_count = 0
        if row.data:
            current_reply_count = int(row.data[0].get("reply_count") or 0)

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
        print(f"⚠ Failed to update outreach_leads reply count: {e}")

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
        print(f"⚠ Failed to update crm_analytics reply count: {e}")


def extract_lead_id(subject: str, msg: Message) -> Optional[int]:
    """
    Extract lead id from email subject.
    Expected format: [lead:123]
    """
    subject = subject or ""
    match = re.search(r"\[lead:(\d+)\]", subject, re.IGNORECASE)
    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


def _extract_message_id(msg: Message) -> str:
    return (msg.get("Message-ID") or msg.get("Message-Id") or "").strip()


def _extract_in_reply_to(msg: Message) -> str:
    return (msg.get("In-Reply-To") or "").strip()


def check_for_replies() -> List[Dict[str, str]]:
    """
    IMAP poll for unread replies, resolve them to leads, persist them,
    and mark them as seen.
    """
    replies: List[Dict[str, str]] = []

    if not IMAP_USER or not IMAP_PASS:
        print("⚠ IMAP_USER / IMAP_PASS not configured")
        return replies

    mail = None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        mail.login(IMAP_USER, IMAP_PASS)
        mail.select(MAILBOX)

        status, messages = mail.search(None, "UNSEEN")
        if status != "OK" or not messages or not messages[0]:
            return replies

        for num in messages[0].split():
            try:
                status, msg_data = mail.fetch(num, "(RFC822)")
                if status != "OK" or not msg_data:
                    continue

                raw_bytes = msg_data[0][1]
                msg = message_from_bytes(raw_bytes)

                subject = _decode_subject(msg.get("Subject"))
                sender = msg.get("From")
                date_value = msg.get("Date")
                message_id = _extract_message_id(msg)
                in_reply_to = _extract_in_reply_to(msg)

                lead_id = extract_lead_id(subject, msg)
                campaign_id = _resolve_campaign_id(lead_id) if lead_id else None

                if not lead_id or not campaign_id:
                    fallback_lead_id, fallback_campaign_id = _resolve_lead_and_campaign_from_sender(sender)
                    lead_id = lead_id or fallback_lead_id
                    campaign_id = campaign_id or fallback_campaign_id

                if not lead_id or not campaign_id:
                    mail.store(num, "+FLAGS", "\\Seen")
                    print(f"⚠ Reply found but lead/campaign could not be resolved: {sender}")
                    continue

                dedupe_key = _reply_key(
                    lead_id=lead_id,
                    campaign_id=campaign_id,
                    subject=subject,
                    sender=sender,
                    message_id=message_id or in_reply_to or None,
                    timestamp=date_value,
                )

                if dedupe_key in _PROCESSED_REPLY_KEYS:
                    mail.store(num, "+FLAGS", "\\Seen")
                    print(f"⚠ Skipping duplicate reply: {dedupe_key}")
                    continue

                _PROCESSED_REPLY_KEYS.add(dedupe_key)

                metadata = {
                    "sender": sender,
                    "subject": subject,
                    "timestamp": date_value or _utc_now_iso(),
                    "source": "imap",
                    "message_id": message_id or None,
                    "gmail_message_id": message_id or None,
                    "thread_id": in_reply_to or message_id or None,
                    "in_reply_to": in_reply_to or None,
                    "channel": "email",
                    "event_key": dedupe_key,
                }

                store_event(
                    lead_id=lead_id,
                    campaign_id=campaign_id,
                    event_type="replied",
                    metadata=metadata,
                )

                _update_reply_metrics(lead_id, campaign_id)

                replies.append(
                    {
                        "lead_id": str(lead_id),
                        "campaign_id": str(campaign_id),
                        "sender": sender or "",
                        "subject": subject,
                        "timestamp": date_value or "",
                    }
                )
                print(f"💬 Reply tracked for lead {lead_id}")

                mail.store(num, "+FLAGS", "\\Seen")

            except Exception as e:
                print(f"⚠ Failed to process one IMAP message: {e}")

        return replies

    except Exception as e:
        print(f"❌ Error checking replies: {e}")
        return replies

    finally:
        try:
            if mail is not None:
                mail.logout()
        except Exception:
            pass


async def start_reply_polling(interval_seconds: int = POLL_INTERVAL_SECONDS) -> None:
    """
    Railway-friendly reply tracking loop.
    """
    print(f"👂 Reply polling started every {interval_seconds}s")
    while True:
        try:
            check_for_replies()
        except Exception as e:
            print(f"⚠ Reply polling error: {e}")

        await asyncio.sleep(interval_seconds)


@app.post("/webhook/inbound_email")
async def inbound_email_webhook(request: Request):
    """
    Optional webhook endpoint if you later connect a provider/webhook source.
    """
    payload = await request.json()

    lead_id = payload.get("lead_id")
    campaign_id = payload.get("campaign_id")
    sender = payload.get("from")
    subject = payload.get("subject")
    timestamp = payload.get("timestamp", _utc_now_iso())
    message_id = payload.get("gmail_message_id") or payload.get("message_id") or payload.get("thread_id")

    if not lead_id:
        return {"status": "error", "message": "lead_id required"}

    try:
        lead_id_int = int(lead_id)
    except Exception:
        return {"status": "error", "message": "lead_id must be an integer"}

    if not campaign_id:
        campaign_id = _resolve_campaign_id(lead_id_int)

    if not campaign_id:
        return {"status": "error", "message": "campaign_id could not be resolved"}

    try:
        campaign_id_int = int(campaign_id)
    except Exception:
        return {"status": "error", "message": "campaign_id must be an integer"}

    dedupe_key = _reply_key(
        lead_id=lead_id_int,
        campaign_id=campaign_id_int,
        subject=subject or "",
        sender=sender,
        message_id=message_id,
        timestamp=timestamp,
    )

    if dedupe_key in _PROCESSED_REPLY_KEYS:
        return {"status": "ok", "message": "duplicate reply skipped"}

    _PROCESSED_REPLY_KEYS.add(dedupe_key)

    metadata = {
        "sender": sender,
        "subject": subject,
        "timestamp": timestamp,
        "source": "webhook",
        "gmail_message_id": message_id,
        "thread_id": message_id,
        "channel": "email",
        "event_key": dedupe_key,
    }

    store_event(
        lead_id=lead_id_int,
        campaign_id=campaign_id_int,
        event_type="replied",
        metadata=metadata,
    )

    _update_reply_metrics(lead_id_int, campaign_id_int)

    return {
        "status": "success",
        "message": f"reply recorded for lead {lead_id_int}",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8010"))
    uvicorn.run(
        "outreach_engine.core.reply_monitor:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )