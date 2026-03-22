# outreach_engine/core/reply_monitor.py

import imaplib
import email
from email.header import decode_header
from typing import List, Dict, Optional
from datetime import datetime

from outreach_engine.tracking.engagement_tracking import track_reply
from outreach_engine.database.event_repository import store_event

# -----------------------------
# IMAP Config
# -----------------------------
IMAP_HOST = "imap.gmail.com"
IMAP_USER = "your_email@gmail.com"
IMAP_PASS = "your_password"
MAILBOX = "INBOX"


# -----------------------------
# IMAP Polling
# -----------------------------
def check_for_replies() -> List[Dict[str, str]]:
    replies = []

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        mail.login(IMAP_USER, IMAP_PASS)
        mail.select(MAILBOX)

        status, messages = mail.search(None, '(UNSEEN)')
        if status != "OK":
            return replies

        for num in messages[0].split():
            status, msg_data = mail.fetch(num, '(RFC822)')
            if status != "OK":
                continue

            msg = email.message_from_bytes(msg_data[0][1])
            subject, encoding = decode_header(msg.get("Subject"))[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8")

            sender = msg.get("From")
            date = msg.get("Date")

            lead_id = extract_lead_id(subject, msg)
            if lead_id:
                store_event(
                    lead_id=lead_id,
                    event_type="replied",
                    metadata={"sender": sender, "subject": subject, "timestamp": date}
                )
                # Track reply in analytics
                track_reply(campaign_id=1)  # TODO: map lead_id → campaign_id

                replies.append({
                    "lead_id": lead_id,
                    "sender": sender,
                    "subject": subject,
                    "timestamp": date
                })

            mail.store(num, '+FLAGS', '\\Seen')

        mail.logout()

    except Exception as e:
        print(f"❌ Error checking replies: {e}")

    return replies


def extract_lead_id(subject: str, msg: email.message.Message) -> Optional[str]:
    import re
    match = re.search(r"\[lead:(\d+)\]", subject)
    if match:
        return match.group(1)
    return None


# -----------------------------
# Provider Webhook
# -----------------------------
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/webhook/inbound_email")
async def inbound_email_webhook(request: Request):
    payload = await request.json()

    lead_id = payload.get("lead_id")
    sender = payload.get("from")
    subject = payload.get("subject")
    timestamp = payload.get("timestamp", datetime.utcnow().isoformat())

    if not lead_id:
        return {"status": "error", "message": "lead_id required"}

    store_event(
        lead_id=lead_id,
        event_type="replied",
        metadata={"sender": sender, "subject": subject, "timestamp": timestamp}
    )

    # Track reply in analytics
    track_reply(campaign_id=1)  # TODO: map lead_id → campaign_id

    return {"status": "success", "message": "reply recorded"}