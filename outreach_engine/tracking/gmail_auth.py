# outreach_engine/tracking/gmail_auth.py

from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

DEFAULT_TOKEN_PATH = "token.pkl"
DEFAULT_CREDENTIALS_PATH = "credentials.json"


def _is_railway() -> bool:
    return any(
        os.getenv(name)
        for name in (
            "RAILWAY_ENVIRONMENT",
            "RAILWAY_PROJECT_ID",
            "RAILWAY_STATIC_URL",
            "RAILWAY_SERVICE_ID",
        )
    )


def _load_pickle_creds(token_path: str) -> Optional[Credentials]:
    path = Path(token_path)
    if not path.exists():
        return None

    try:
        with path.open("rb") as f:
            creds = pickle.load(f)
        if isinstance(creds, Credentials):
            return creds
    except Exception as e:
        print(f"⚠ Failed to load token pickle: {e}")

    return None


def _save_pickle_creds(creds: Credentials, token_path: str) -> None:
    try:
        path = Path(token_path)
        with path.open("wb") as f:
            pickle.dump(creds, f)
    except Exception as e:
        print(f"⚠ Failed to save token pickle: {e}")


def _load_env_creds() -> Optional[Credentials]:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
    access_token = os.getenv("GOOGLE_ACCESS_TOKEN")
    token_uri = os.getenv("GOOGLE_TOKEN_URI", "https://oauth2.googleapis.com/token")

    if not (client_id and client_secret and refresh_token):
        return None

    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


def authenticate() -> Credentials:
    """
    Railway-safe Gmail auth:

    1) Try token.pkl (local dev)
    2) Try env-based OAuth creds (best for Railway)
    3) If not on Railway, fall back to local interactive login via credentials.json

    On Railway, you should set:
    - GOOGLE_CLIENT_ID
    - GOOGLE_CLIENT_SECRET
    - GOOGLE_REFRESH_TOKEN
    """
    token_path = os.getenv("GMAIL_TOKEN_PATH", DEFAULT_TOKEN_PATH)
    credentials_path = os.getenv("GMAIL_CREDENTIALS_PATH", DEFAULT_CREDENTIALS_PATH)

    creds = _load_pickle_creds(token_path)
    if not creds:
        creds = _load_env_creds()

    if creds:
        try:
            if not creds.valid:
                if creds.refresh_token:
                    creds.refresh(Request())
                else:
                    raise RuntimeError("Gmail credentials are not valid and have no refresh token.")
        except RefreshError as e:
            print(f"⚠ Gmail token refresh failed: {e}")
            creds = None

    if creds is None and not _is_railway():
        cred_file = Path(credentials_path)
        if cred_file.exists():
            flow = InstalledAppFlow.from_client_secrets_file(str(cred_file), SCOPES)
            creds = flow.run_local_server(port=0)
            _save_pickle_creds(creds, token_path)
        else:
            raise RuntimeError(
                "Gmail auth failed. Provide token.pkl locally, or set Google OAuth env vars."
            )

    if creds is None:
        raise RuntimeError(
            "Gmail auth failed on Railway. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN."
        )

    if not _is_railway():
        _save_pickle_creds(creds, token_path)

    print("✅ Gmail authenticated")
    return creds