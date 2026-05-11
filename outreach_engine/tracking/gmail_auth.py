# outreach_engine/tracking/gmail_auth.py

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import Any, Optional

try:
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials

    GOOGLE_LIBS_AVAILABLE = True
except Exception:
    RefreshError = Exception
    GoogleRequest = None
    Credentials = None
    GOOGLE_LIBS_AVAILABLE = False


SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

DEFAULT_TOKEN_JSON_PATH = "token.json"
DEFAULT_CREDENTIALS_PATH = "credentials.json"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return _project_root() / path


def _is_railway() -> bool:
    return any(
        os.getenv(name)
        for name in (
            "RAILWAY_ENVIRONMENT",
            "RAILWAY_PROJECT_ID",
            "RAILWAY_STATIC_URL",
            "RAILWAY_SERVICE_ID",
            "RAILWAY_PUBLIC_DOMAIN",
        )
    )


def _non_interactive_mode() -> bool:
    mode = os.getenv("GMAIL_AUTH_MODE", "").strip().lower()
    if mode in {"server", "noninteractive", "non-interactive"}:
        return True
    if os.getenv("GMAIL_DISABLE_INTERACTIVE_AUTH", "true").strip().lower() == "true":
        return True
    return _is_railway()


def _creds_from_json_data(data: dict) -> Optional[Any]:
    if not GOOGLE_LIBS_AVAILABLE or Credentials is None:
        return None
    try:
        return Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=data.get("scopes") or SCOPES,
        )
    except Exception as e:
        print(f"⚠ Failed to build creds from JSON data: {e}")
        return None


def _load_b64_creds() -> Optional[Any]:
    """
    Load credentials from GMAIL_TOKEN_B64 env var (Railway/server use).
    Supports both JSON and pickle formats.
    """
    token_b64 = os.getenv("GMAIL_TOKEN_B64", "").strip()
    if not token_b64:
        return None

    print("✅ Using GMAIL_TOKEN_B64")

    try:
        token_bytes = base64.b64decode(token_b64)
    except Exception as e:
        print(f"⚠ Failed to base64-decode GMAIL_TOKEN_B64: {e}")
        return None

    # Try JSON first
    try:
        parsed = json.loads(token_bytes.decode("utf-8"))
        creds = _creds_from_json_data(parsed)
        if creds:
            print("✅ Loaded GMAIL_TOKEN_B64 as JSON")
            return creds
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    # Try pickle as fallback
    try:
        import pickle
        creds = pickle.load(io.BytesIO(token_bytes))
        print("✅ Loaded GMAIL_TOKEN_B64 as pickle")
        return creds
    except Exception as e:
        print(f"⚠ Failed to load GMAIL_TOKEN_B64 as pickle: {e}")

    return None


def _load_json_creds(token_json_path: str) -> Optional[Any]:
    if not GOOGLE_LIBS_AVAILABLE or Credentials is None:
        return None

    path = _resolve_path(token_json_path)
    if not path.exists():
        print(f"⚠ token.json not found: {path}")
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(path), SCOPES)
        print(f"📄 Loaded Gmail token from: {path}")
        return creds
    except Exception as e:
        print(f"⚠ Failed to load token.json: {e}")
        return None


def _save_json_creds(creds: Any, token_json_path: str) -> None:
    try:
        if hasattr(creds, "to_json"):
            path = _resolve_path(token_json_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(creds.to_json(), encoding="utf-8")
            print(f"💾 Saved Gmail token to: {path}")
    except Exception as e:
        print(f"⚠ Failed to save token.json: {e}")


def _load_env_creds() -> Optional[Any]:
    if not GOOGLE_LIBS_AVAILABLE or Credentials is None:
        return None

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
    access_token = os.getenv("GOOGLE_ACCESS_TOKEN")
    token_uri = os.getenv("GOOGLE_TOKEN_URI", "https://oauth2.googleapis.com/token")

    if not (client_id and client_secret and refresh_token):
        return None

    try:
        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=token_uri,
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        print("🔑 Loaded Gmail creds from environment")
        return creds
    except Exception as e:
        print(f"⚠ Failed to build env credentials: {e}")
        return None


def _refresh_if_needed(creds: Any, token_json_path: str) -> Any:
    if not GOOGLE_LIBS_AVAILABLE or GoogleRequest is None:
        return creds

    try:
        expired = bool(getattr(creds, "expired", False))
        valid = getattr(creds, "valid", None)
        refresh_token = getattr(creds, "refresh_token", None)
        token = getattr(creds, "token", None)

        needs_refresh = bool(refresh_token) and (
            expired or valid is False or not token
        )

        if needs_refresh:
            print("♻ Refreshing Gmail credentials...")
            creds.refresh(GoogleRequest())
            print("✅ Gmail credentials refreshed.")
            _save_json_creds(creds, token_json_path)

    except RefreshError as e:
        print(f"⚠ Gmail token refresh failed: {e}")
        raise
    except Exception as e:
        print(f"⚠ Gmail credential refresh failed: {e}")
        raise

    return creds


def authenticate() -> Any:
    """
    Priority:
    1) GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN env vars
    2) GMAIL_TOKEN_B64 env var (Railway)
    3) token.json file (local)
    4) Browser OAuth (local only, never on server)
    """
    token_json_path = os.getenv("GMAIL_TOKEN_JSON_PATH", DEFAULT_TOKEN_JSON_PATH)
    credentials_path = os.getenv("GMAIL_CREDENTIALS_PATH", DEFAULT_CREDENTIALS_PATH)

    print("🔐 Gmail auth start")

    # 1) ENV CREDS (GOOGLE_CLIENT_ID etc.)
    creds = _load_env_creds()
    if creds is not None:
        creds = _refresh_if_needed(creds, token_json_path)
        if getattr(creds, "token", None):
            _save_json_creds(creds, token_json_path)
            print("✅ Gmail authenticated (env)")
            return creds

    # 2) GMAIL_TOKEN_B64 (Railway / server)
    creds = _load_b64_creds()
    if creds is not None:
        creds = _refresh_if_needed(creds, token_json_path)
        if getattr(creds, "token", None):
            print("✅ Gmail authenticated (GMAIL_TOKEN_B64)")
            return creds

    # 3) token.json (local)
    creds = _load_json_creds(token_json_path)
    if creds is not None:
        if getattr(creds, "valid", False):
            print("✅ Gmail authenticated (cached token)")
            return creds
        if getattr(creds, "refresh_token", None):
            creds = _refresh_if_needed(creds, token_json_path)
            if getattr(creds, "token", None):
                print("✅ Gmail authenticated (refreshed token)")
                return creds

    # 4) NEVER browser in server mode
    if _non_interactive_mode():
        raise RuntimeError(
            "Gmail auth failed in non-interactive mode. "
            "Provide GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN, "
            "or set GMAIL_TOKEN_B64 to a base64-encoded token.json."
        )

    # 5) Local OAuth flow
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except Exception as e:
        raise RuntimeError("google-auth-oauthlib missing. pip install google-auth-oauthlib") from e

    cred_file = _resolve_path(credentials_path)
    if not cred_file.exists():
        raise RuntimeError(f"credentials.json not found: {cred_file}")

    print(f"🌐 Starting local Gmail OAuth flow using: {cred_file}")
    flow = InstalledAppFlow.from_client_secrets_file(str(cred_file), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_json_creds(creds, token_json_path)
    print("✅ Gmail authenticated (local OAuth)")
    return creds
