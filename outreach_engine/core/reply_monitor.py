# outreach_engine/core/reply_monitor.py

import imaplib
import email
import re
from email.header import decode_header
from typing import List, Dict, Optional, Any
from datetime import datetime

from fastapi import FastAPI, Request

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.engagement_tracking import track_reply

# -----------------------------
# IMAP Config
# -----------------------------
IMAP_HOST = "imap.gmail.com"
IMAP_USER = "your_email@gmail.com"
IMAP_PASS = "your_password"
MAILBOX = "INBOX"


app = FastAPI(title="Outreach Engine Reply Monitor")


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
            return res.data[0].get("campaign_id")
    except Exception as e:
        print(f"⚠ Failed to resolve campaign_id for reply tracking: {e}")
    return None


def extract_lead_id(subject: str, msg: email.message.Message) -> Optional[int]:
    """
    Extract lead id from email subject.
    Expected format: [lead:123]
    """
    match = re.search(r"\[lead:(\d+)\]", subject or "")
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


# -----------------------------
# IMAP Polling
# -----------------------------
def check_for_replies() -> List[Dict[str, str]]:
    replies: List[Dict[str, str]] = []

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        mail.login(IMAP_USER, IMAP_PASS)
        mail.select(MAILBOX)

        status, messages = mail.search(None, "(UNSEEN)")
        if status != "OK":
            return replies

        for num in messages[0].split():
            status, msg_data = mail.fetch(num, "(RFC822)")
            if status != "OK":
                continue

            msg = email.message_from_bytes(msg_data[0][1])

            subject, encoding = decode_header(msg.get("Subject"))[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8", errors="ignore")

            sender = msg.get("From")
            date_value = msg.get("Date")

            lead_id = extract_lead_id(subject, msg)
            if lead_id:
                campaign_id = _resolve_campaign_id(lead_id)
                metadata = {
                    "sender": sender,
                    "subject": subject,
                    "timestamp": date_value or datetime.utcnow().isoformat(),
                    "source": "imap",
                }

                if campaign_id:
                    track_reply(
                        lead_id=lead_id,
                        campaign_id=campaign_id,
                        metadata=metadata
                    )

                    replies.append({
                        "lead_id": str(lead_id),
                        "campaign_id": str(campaign_id),
                        "sender": sender,
                        "subject": subject,
                        "timestamp": date_value,
                    })
                else:
                    print(f"⚠ Reply found but campaign_id not resolved for lead {lead_id}")

            mail.store(num, "+FLAGS", "\\Seen")

        mail.logout()

    except Exception as e:
        print(f"❌ Error checking replies: {e}")

    return replies


# -----------------------------
# Provider Webhook
# -----------------------------
@app.post("/webhook/inbound_email")
async def inbound_email_webhook(request: Request):
    payload = await request.json()

    lead_id = payload.get("lead_id")
    campaign_id = payload.get("campaign_id")
    sender = payload.get("from")
    subject = payload.get("subject")
    timestamp = payload.get("timestamp", datetime.utcnow().isoformat())

    if not lead_id:
        return {"status": "error", "message": "lead_id required"}

    if not campaign_id:
        campaign_id = _resolve_campaign_id(int(lead_id))

    if not campaign_id:
        return {"status": "error", "message": "campaign_id could not be resolved"}

    metadata = {
        "sender": sender,
        "subject": subject,
        "timestamp": timestamp,
        "source": "webhook",
    }

    track_reply(
        lead_id=int(lead_id),
        campaign_id=int(campaign_id),
        metadata=metadata
    )

    return {
        "status": "success",
        "message": f"reply recorded for lead {lead_id}"
    }