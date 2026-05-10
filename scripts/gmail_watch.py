# scripts/gmail_watch.py

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

TOPIC_NAME = os.getenv(
    "GMAIL_PUBSUB_TOPIC",
    "projects/make-487214/topics/gmail-replies",
).strip()

ROOT_DIR = Path(__file__).resolve().parents[1]
TOKEN_JSON_PATH = Path(os.getenv("GMAIL_TOKEN_JSON_PATH", str(ROOT_DIR / "token.json")))


def _load_creds_from_b64() -> Credentials | None:
    token_b64 = os.getenv("GMAIL_TOKEN_B64", "").strip()
    if not token_b64:
        return None

    try:
        token_bytes = base64.b64decode(token_b64)
        token_info = json.loads(token_bytes.decode("utf-8"))

        if not isinstance(token_info, dict):
            raise ValueError("Decoded token is not a JSON object")

        return Credentials.from_authorized_user_info(token_info, scopes=SCOPES)

    except Exception as e:
        raise Exception(f"Failed to load Gmail credentials from GMAIL_TOKEN_B64: {e}") from e


def _load_creds_from_file() -> Credentials:
    if TOKEN_JSON_PATH.exists():
        try:
            return Credentials.from_authorized_user_file(str(TOKEN_JSON_PATH), SCOPES)
        except Exception as e:
            raise Exception(f"Failed to load token.json: {e}") from e

    raise FileNotFoundError(f"Missing Gmail token. Looked for: {TOKEN_JSON_PATH}")


def _save_token_json(creds: Credentials) -> None:
    if hasattr(creds, "to_json"):
        TOKEN_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_JSON_PATH.write_text(creds.to_json(), encoding="utf-8")


def get_service():
    creds = _load_creds_from_b64()
    if creds is None:
        creds = _load_creds_from_file()

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token_json(creds)

    _save_token_json(creds)

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def main() -> None:
    service = get_service()

    response = service.users().watch(
        userId="me",
        body={
            "labelIds": ["INBOX"],
            "topicName": TOPIC_NAME,
        },
    ).execute()

    print("✅ WATCH RESPONSE:", response)


if __name__ == "__main__":
    main()