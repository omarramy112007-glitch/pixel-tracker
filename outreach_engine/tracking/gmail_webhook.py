# outreach_engine/tracking/gmail_webhook.py

from fastapi import APIRouter, Request
from googleapiclient.discovery import build
import base64
import json
import pickle
import re
from datetime import datetime
from typing import Optional, Tuple, Any

from outreach_engine.tracking.engagement_tracking import track_reply
from outreach_engine.database.supabase_client import supabase

router = APIRouter()

STATE_KEY = "main"


# -------------------------------
# Gmail Service
# -------------------------------
def get_service():
    with open("token.pkl", "rb") as f:
        creds = pickle.load(f)
    return build("gmail", "v1", credentials=creds)


# -------------------------------
# Helpers
# -------------------------------
def _extract_email_from_from_header(raw_from: str) -> str:
    if not raw_from:
        return ""

    raw_from = raw_from.strip()
    match = re.search(r"<(.+?)>", raw_from)
    return match.group(1).strip().lower() if match else raw_from.lower()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _get_gmail_state() -> Tuple[Optional[int], Optional[str]]:
    try:
        res = (
            supabase.table("gmail_state")
            .select("*")
            .eq("key", STATE_KEY)
            .limit(1)
            .execute()
        )
        if res.data:
            row = res.data[0]
            return row.get("id"), row.get("last_history_id")
    except Exception as e:
        print(f"⚠ Failed to read gmail_state: {e}")

    return None, None


def _set_gmail_state(history_id: str) -> None:
    try:
        supabase.table("gmail_state").upsert({
            "key": STATE_KEY,
            "last_history_id": str(history_id),
            "updated_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        print(f"⚠ Failed to update gmail_state: {e}")


def _already_processed(message_id: str) -> bool:
    try:
        res = (
            supabase.table("lead_events")
            .select("id")
            .eq("gmail_message_id", message_id)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception:
        return False


def _reserve_message(
    lead_id: int,
    campaign_id: int,
    message_id: str,
    thread_id: str,
    history_id: str
) -> bool:
    """
    Reserve this Gmail message before tracking it.
    The unique gmail_message_id index prevents duplicate processing.
    """
    try:
        supabase.table("lead_events").insert({
            "lead_id": lead_id,
            "campaign_id": campaign_id,
            "event_type": "gmail_reply_processed",
            "gmail_message_id": message_id,
            "metadata": {
                "channel": "email",
                "thread_id": thread_id,
                "history_id": history_id,
            },
            "timestamp": datetime.utcnow().isoformat(),
        }).execute()
        return True

    except Exception as e:
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg:
            print(f"⛔ Duplicate message blocked: {message_id}")
            return False

        print(f"⚠ Failed to reserve Gmail message: {e}")
        return False


def is_real_reply(message) -> bool:
    headers = message.get("payload", {}).get("headers", [])

    from_email = ""
    in_reply_to = ""
    references = ""

    for h in headers:
        name = h.get("name")
        value = h.get("value", "")

        if name == "From":
            from_email = _extract_email_from_from_header(value)
        elif name == "In-Reply-To":
            in_reply_to = value
        elif name == "References":
            references = value

    if not in_reply_to and not references:
        return False

    blocked_keywords = [
        "noreply", "no-reply", "notifications", "updates",
        "linkedin", "pinterest", "reddit", "duolingo",
        "mailchimp", "sendgrid", "stripe", "notion", "google"
    ]

    if any(x in from_email for x in blocked_keywords):
        return False

    return True


def extract_lead_from_email(message) -> Tuple[Optional[int], Optional[int], str, str]:
    headers = message.get("payload", {}).get("headers", [])

    subject = ""
    from_email = ""

    for h in headers:
        if h.get("name") == "Subject":
            subject = h.get("value", "")
        elif h.get("name") == "From":
            raw_from = h.get("value", "")
            from_email = _extract_email_from_from_header(raw_from)

    if not from_email:
        return None, None, "", subject

    # Prefer thread_id if present on the outbound lead
    thread_id = message.get("threadId")
    if thread_id:
        try:
            res = (
                supabase.table("outreach_leads")
                .select("id, campaign_id, email, thread_id")
                .eq("thread_id", thread_id)
                .limit(1)
                .execute()
            )
            if res.data:
                row = res.data[0]
                return row["id"], row["campaign_id"], from_email, subject
        except Exception:
            pass

    # Fallback: exact email match
    try:
        res = (
            supabase.table("outreach_leads")
            .select("id, campaign_id, email")
            .eq("email", from_email)
            .limit(1)
            .execute()
        )
        if res.data:
            row = res.data[0]
            return row["id"], row["campaign_id"], from_email, subject
    except Exception as e:
        print(f"⚠ lead lookup failed: {e}")

    return None, None, from_email, subject


# -------------------------------
# Webhook
# -------------------------------
@router.post("/gmail/webhook")
async def gmail_webhook(request: Request):
    print("📩 Gmail push received")

    body = await request.json()
    message_data = body.get("message", {}).get("data")
    if not message_data:
        return {"status": "ok", "message": "no message data"}

    decoded = base64.b64decode(message_data).decode("utf-8")

    try:
        payload = json.loads(decoded)
    except Exception:
        return {"status": "ok", "message": "invalid pubsub payload"}

    incoming_history_id = payload.get("historyId")
    print(f"📨 History ID: {incoming_history_id}")

    if not incoming_history_id:
        return {"status": "ok", "message": "no historyId"}

    incoming_history_int = _to_int(incoming_history_id, 0)
    _, last_history_id = _get_gmail_state()
    last_history_int = _to_int(last_history_id, 0) if last_history_id else 0

    # First run: initialize cursor and exit
    if last_history_int == 0:
        _set_gmail_state(incoming_history_id)
        return {"status": "ok", "message": "gmail state initialized"}

    # Ignore old / repeated webhook windows
    if incoming_history_int <= last_history_int:
        print(f"⛔ Old historyId skipped: {incoming_history_id}")
        return {"status": "ok", "message": "already processed"}

    service = get_service()

    try:
        history = service.users().history().list(
            userId="me",
            startHistoryId=str(last_history_id),
            historyTypes=["messageAdded"]
        ).execute()
    except Exception as e:
        print(f"⚠ Gmail history fetch failed: {e}")
        return {"status": "ok", "message": "history fetch failed"}

    processed_max_history_int = last_history_int

    for record in history.get("history", []):
        record_history_int = _to_int(record.get("id"), processed_max_history_int)
        processed_max_history_int = max(processed_max_history_int, record_history_int)

        for msg in record.get("messagesAdded", []):
            message_id = msg["message"]["id"]
            thread_id = msg["message"].get("threadId")

            if not message_id or not thread_id:
                continue

            if _already_processed(message_id):
                continue

            try:
                message = service.users().messages().get(
                    userId="me",
                    id=message_id,
                    format="full"
                ).execute()
            except Exception as e:
                print(f"⚠ Gmail message fetch failed: {e}")
                continue

            if not is_real_reply(message):
                continue

            lead_id, campaign_id, from_email, subject = extract_lead_from_email(message)

            print(f"DEBUG → From: {from_email} | Subject: {subject} | Thread: {thread_id}")

            if lead_id and campaign_id:
                # Reserve first to block duplicates immediately
                reserved = _reserve_message(
                    lead_id=lead_id,
                    campaign_id=campaign_id,
                    message_id=message_id,
                    thread_id=thread_id,
                    history_id=str(incoming_history_id),
                )

                if not reserved:
                    continue

                try:
                    track_reply(
                        lead_id=lead_id,
                        campaign_id=campaign_id,
                        metadata={
                            "channel": "email",
                            "from_email": from_email,
                            "subject": subject,
                            "gmail_message_id": message_id,
                            "thread_id": thread_id,
                            "history_id": incoming_history_id,
                        }
                    )
                    print(f"💬 Reply tracked for lead {lead_id}")
                except Exception as e:
                    print(f"⚠ track_reply failed for lead {lead_id}: {e}")
            else:
                print(f"⚠ No matching lead found for {from_email}")

    # Move cursor forward only after processing
    _set_gmail_state(str(processed_max_history_int or incoming_history_id))

    return {"status": "ok"}