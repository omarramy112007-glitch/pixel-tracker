# scripts/gmail_watch.py

from __future__ import annotations

import base64
import json
import os
import pickle
from pathlib import Path

from dotenv import load_dotenv
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
TOKEN_PICKLE_PATH = Path(os.getenv("GMAIL_TOKEN_PATH", str(ROOT_DIR / "token.pkl")))


def _load_creds_from_b64() -> Credentials | None:
    token_b64 = os.getenv("GMAIL_TOKEN_B64", "").strip()
    if not token_b64:
        return None

    try:
        token_bytes = base64.b64decode(token_b64)

        # Preferred format: JSON token
        try:
            token_info = json.loads(token_bytes.decode("utf-8"))
            if isinstance(token_info, dict):
                return Credentials.from_authorized_user_info(token_info, scopes=SCOPES)
        except Exception:
            pass

        # Legacy fallback: pickled Credentials
        try:
            return pickle.loads(token_bytes)
        except Exception as e:
            raise Exception(f"GMAIL_TOKEN_B64 is neither JSON nor pickle: {e}") from e

    except Exception as e:
        raise Exception(f"Failed to load Gmail credentials from GMAIL_TOKEN_B64: {e}") from e


def _load_creds_from_file() -> Credentials:
    if TOKEN_JSON_PATH.exists():
        try:
            return Credentials.from_authorized_user_file(str(TOKEN_JSON_PATH), SCOPES)
        except Exception as e:
            raise Exception(f"Failed to load token.json: {e}") from e

    if TOKEN_PICKLE_PATH.exists():
        try:
            with TOKEN_PICKLE_PATH.open("rb") as f:
                return pickle.load(f)
        except Exception as e:
            raise Exception(f"Failed to load token.pkl: {e}") from e

    raise FileNotFoundError(
        f"Missing Gmail token. Looked for: {TOKEN_JSON_PATH} and {TOKEN_PICKLE_PATH}"
    )


def get_service():
    creds = _load_creds_from_b64()
    if creds is None:
        creds = _load_creds_from_file()

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