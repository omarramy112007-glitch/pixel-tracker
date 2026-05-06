from __future__ import annotations

import base64
import os
import pickle

from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv()

TOPIC_NAME = os.getenv(
    "GMAIL_PUBSUB_TOPIC",
    "projects/make-487214/topics/gmail-replies",
).strip()

def get_service():
    token_b64 = os.getenv("GMAIL_TOKEN_B64", "").strip()
    if not token_b64:
        raise Exception("Missing GMAIL_TOKEN_B64")

    try:
        token_bytes = base64.b64decode(token_b64)
        creds = pickle.loads(token_bytes)
    except Exception as e:
        raise Exception(f"Failed to load Gmail credentials from GMAIL_TOKEN_B64: {e}")

    return build("gmail", "v1", credentials=creds, cache_discovery=False)

service = get_service()

response = service.users().watch(
    userId="me",
    body={
        "labelIds": ["INBOX"],
        "topicName": TOPIC_NAME,
    },
).execute()

print("✅ WATCH RESPONSE:", response)