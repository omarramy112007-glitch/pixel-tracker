# outreach_engine/tracking/gmail_watcher.py

from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any, Dict, Optional

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

PROJECT_ID = os.getenv("GMAIL_PROJECT_ID", "make-487214").strip()
TOPIC_NAME = os.getenv("GMAIL_PUBSUB_TOPIC", "gmail-replies").strip()
TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.pkl").strip()
HISTORY_PATH = os.getenv("GMAIL_HISTORY_PATH", "gmail_history_id.txt").strip()

# Gmail watch usually needs a scope like:
# https://www.googleapis.com/auth/gmail.modify
# If your token was created only with gmail.send, watch may fail.
REQUIRED_WATCH_SCOPES = {
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.metadata",
}


def _load_credentials(token_path: str = TOKEN_PATH) -> Credentials:
    """
    Load Gmail OAuth credentials from a pickle file.

    Supports:
    - google.oauth2.credentials.Credentials
    - dict-like payloads that can be converted into Credentials
    """
    if not os.path.exists(token_path):
        raise FileNotFoundError(
            f"Missing OAuth token file: {token_path}. "
            "Run the Gmail auth step first to create it."
        )

    with open(token_path, "rb") as f:
        data = pickle.load(f)

    # Already a Credentials object
    if isinstance(data, Credentials):
        creds = data
    # Dict-like fallback
    elif isinstance(data, dict):
        creds = Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=data.get("scopes"),
        )
    else:
        raise TypeError(
            f"Unsupported token format in {token_path}. "
            "Expected google.oauth2.credentials.Credentials or a dict."
        )

    return creds


def _save_credentials(creds: Credentials, token_path: str = TOKEN_PATH) -> None:
    """
    Persist refreshed credentials back to disk.
    """
    with open(token_path, "wb") as f:
        pickle.dump(creds, f)


def _ensure_credentials_valid(creds: Credentials) -> Credentials:
    """
    Refresh expired credentials if possible.
    """
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
            _save_credentials(creds)
            print("✅ Gmail credentials refreshed and saved.")
        except Exception as e:
            raise RuntimeError(
                f"Failed to refresh Gmail credentials: {e}"
            ) from e

    if not creds.valid:
        raise RuntimeError(
            "Gmail credentials are invalid. "
            "Re-run the Gmail OAuth flow to generate a fresh token."
        )

    return creds


def _build_topic_name(project_id: str, topic_name: str) -> str:
    if not topic_name:
        raise ValueError("GMAIL_PUBSUB_TOPIC is empty.")

    if topic_name.startswith("projects/"):
        return topic_name

    if not project_id:
        raise ValueError("GMAIL_PROJECT_ID is empty.")

    return f"projects/{project_id}/topics/{topic_name}"


def _persist_watch_response(response: Dict[str, Any], path: str = HISTORY_PATH) -> None:
    """
    Save Gmail watch metadata so the historyId can be reused later.
    """
    history_id = response.get("historyId")
    expiration = response.get("expiration")

    payload = {
        "historyId": history_id,
        "expiration": expiration,
    }

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(payload))
        print(f"💾 Watch metadata saved to {path}")
    except Exception as e:
        print(f"⚠ Failed to save watch metadata: {e}")


def start_watch() -> Dict[str, Any]:
    """
    Start Gmail push notifications for the authenticated mailbox.

    Requirements:
    - Gmail API enabled
    - token.pkl created by OAuth login
    - Pub/Sub topic already created
    - Pub/Sub topic configured to allow Gmail publish

    Best-practice scope:
    - https://www.googleapis.com/auth/gmail.modify
    """
    creds = _ensure_credentials_valid(_load_credentials())
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    request_body = {
        "labelIds": ["INBOX"],
        "topicName": _build_topic_name(PROJECT_ID, TOPIC_NAME),
    }

    try:
        response = service.users().watch(userId="me", body=request_body).execute()

        print("👀 Watch started:", response)
        _persist_watch_response(response)

        # Helpful warning if the token scopes are too limited
        scopes = set(creds.scopes or [])
        if scopes and not (scopes & REQUIRED_WATCH_SCOPES):
            print(
                "⚠ Your token scopes may be too limited for Gmail watch. "
                "If watch fails later, re-auth with gmail.modify."
            )

        return response

    except HttpError as e:
        content = getattr(e, "content", None)
        if content:
            print(f"❌ Gmail watch failed: {content}")
        else:
            print(f"❌ Gmail watch failed: {e}")
        raise

    except Exception as e:
        print(f"❌ Unexpected error while starting Gmail watch: {e}")
        raise


if __name__ == "__main__":
    start_watch()