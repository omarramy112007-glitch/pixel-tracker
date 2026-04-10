# outreach_engine/tracking/gmail_watcher.py

from __future__ import annotations

import os
import pickle
from typing import Any, Dict

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

PROJECT_ID = "make-487214"
TOPIC_NAME = "gmail-replies"  # <-- replace with your actual Pub/Sub topic name
TOKEN_PATH = "token.pkl"


def _load_credentials(token_path: str = TOKEN_PATH):
    if not os.path.exists(token_path):
        raise FileNotFoundError(
            f"Missing OAuth token file: {token_path}. "
            "Run the Gmail auth step first to create it."
        )

    with open(token_path, "rb") as f:
        return pickle.load(f)


def start_watch() -> Dict[str, Any]:
    """
    Start Gmail push notifications for the authenticated mailbox.

    Requires:
    - Gmail API enabled
    - token.pkl created by OAuth login
    - Pub/Sub topic already created
    - Pub/Sub topic configured to allow Gmail publish
    """
    creds = _load_credentials()
    service = build("gmail", "v1", credentials=creds)

    request_body = {
        "labelIds": ["INBOX"],
        "topicName": f"projects/{PROJECT_ID}/topics/{TOPIC_NAME}",
    }

    try:
        response = service.users().watch(userId="me", body=request_body).execute()
        print("👀 Watch started:", response)
        return response
    except HttpError as e:
        print(f"❌ Gmail watch failed: {e}")
        raise
    except Exception as e:
        print(f"❌ Unexpected error while starting Gmail watch: {e}")
        raise


if __name__ == "__main__":
    start_watch()