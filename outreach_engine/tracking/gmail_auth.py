# outreach_engine/tracking/gmail_auth.py

from __future__ import annotations

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


def _load_json_creds(token_json_path: str) -> Optional[Any]:
    """
    Load token.json FAST without browser flow.
    """
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
    """
    Server-side auth via env vars.
    """

    if not GOOGLE_LIBS_AVAILABLE or Credentials is None:
        return None

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
    access_token = os.getenv("GOOGLE_ACCESS_TOKEN")

    token_uri = os.getenv(
        "GOOGLE_TOKEN_URI",
        "https://oauth2.googleapis.com/token",
    )

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
    """
    Refresh ONLY if needed.
    Never trigger browser flow here.
    """

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
    PRIORITY:

    1) ENV CREDS
    2) token.json
    3) Browser OAuth (local only)

    NEVER opens browser if token.json is valid.
    """

    token_json_path = os.getenv(
        "GMAIL_TOKEN_JSON_PATH",
        DEFAULT_TOKEN_JSON_PATH,
    )

    credentials_path = os.getenv(
        "GMAIL_CREDENTIALS_PATH",
        DEFAULT_CREDENTIALS_PATH,
    )

    print("🔐 Gmail auth start")

    # =========================================================
    # 1) ENV CREDS
    # =========================================================

    creds = _load_env_creds()

    if creds is not None:
        creds = _refresh_if_needed(creds, token_json_path)

        token = getattr(creds, "token", None)

        if token:
            _save_json_creds(creds, token_json_path)

            print("✅ Gmail authenticated (env)")

            return creds

    # =========================================================
    # 2) token.json
    # =========================================================

    creds = _load_json_creds(token_json_path)

    if creds is not None:

        # VALID TOKEN → RETURN IMMEDIATELY
        if getattr(creds, "valid", False):
            print("✅ Gmail authenticated (cached token)")
            return creds

        # EXPIRED BUT REFRESHABLE
        refresh_token = getattr(creds, "refresh_token", None)

        if refresh_token:
            creds = _refresh_if_needed(creds, token_json_path)

            token = getattr(creds, "token", None)

            if token:
                print("✅ Gmail authenticated (refreshed token)")
                return creds

    # =========================================================
    # 3) NEVER BROWSER IN SERVER MODE
    # =========================================================

    if _non_interactive_mode():
        raise RuntimeError(
            "Gmail auth failed in non-interactive mode. "
            "Provide GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, "
            "GOOGLE_REFRESH_TOKEN, or a valid token.json."
        )

    # =========================================================
    # 4) LOCAL OAUTH FLOW
    # =========================================================

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

    except Exception as e:
        raise RuntimeError(
            "google-auth-oauthlib missing. "
            "Install with: pip install google-auth-oauthlib"
        ) from e

    cred_file = _resolve_path(credentials_path)

    if not cred_file.exists():
        raise RuntimeError(
            f"credentials.json not found: {cred_file}"
        )

    print(f"🌐 Starting local Gmail OAuth flow using: {cred_file}")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(cred_file),
        SCOPES,
    )

    creds = flow.run_local_server(port=0)

    _save_json_creds(creds, token_json_path)

    print("✅ Gmail authenticated (local OAuth)")

    return creds