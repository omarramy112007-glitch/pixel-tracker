# outreach_engine/tracking/generate_token.py

from __future__ import annotations

import base64
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
TOKEN_JSON_PATH = os.getenv("GMAIL_TOKEN_JSON_PATH", "token.json")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_path(value: str) -> Path:
    p = Path(value)
    if p.is_absolute():
        return p
    return _project_root() / p


def main() -> None:
    cred_file = _resolve_path(CREDENTIALS_PATH)
    if not cred_file.exists():
        raise FileNotFoundError(f"Missing {cred_file}")

    flow = InstalledAppFlow.from_client_secrets_file(str(cred_file), SCOPES)
    creds = flow.run_local_server(port=0)

    token_json_file = _resolve_path(TOKEN_JSON_PATH)
    token_json_file.parent.mkdir(parents=True, exist_ok=True)

    token_json = creds.to_json()
    token_json_file.write_text(token_json, encoding="utf-8")

    token_b64 = base64.b64encode(token_json.encode("utf-8")).decode("utf-8")

    print(f"✅ token.json created successfully: {token_json_file}")
    print("\nCopy this into Railway / env as GMAIL_TOKEN_B64:\n")
    print(token_b64)


if __name__ == "__main__":
    main()