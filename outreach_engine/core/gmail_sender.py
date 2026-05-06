# outreach_engine/core/gmail_sender.py

from __future__ import annotations

import base64
import json
import os
import pickle
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path
from typing import Any, Dict, Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

BASE_DIR = Path(__file__).resolve().parents[2]
TOKEN_JSON_PATH = BASE_DIR / "token.json"
TOKEN_PKL_PATH = BASE_DIR / "token.pkl"
CREDENTIALS_JSON_PATH = BASE_DIR / "credentials.json"

FROM_EMAIL = os.getenv("GMAIL_FROM") or os.getenv("GMAIL_USER")


def _load_credentials_from_env() -> Optional[Credentials]:
    token_b64 = os.getenv("GMAIL_TOKEN_B64", "").strip()
    if not token_b64:
        return None

    try:
        raw = base64.b64decode(token_b64)
    except Exception:
        return None

    try:
        creds_obj = pickle.loads(raw)
        if isinstance(creds_obj, Credentials):
            return creds_obj
    except Exception:
        pass

    try:
        data = json.loads(raw.decode("utf-8"))
        return Credentials.from_authorized_user_info(data, SCOPES)
    except Exception:
        return None


def _load_credentials_from_disk() -> Optional[Credentials]:
    if TOKEN_JSON_PATH.exists():
        try:
            return Credentials.from_authorized_user_file(str(TOKEN_JSON_PATH), SCOPES)
        except Exception:
            pass

    if TOKEN_PKL_PATH.exists():
        try:
            with open(TOKEN_PKL_PATH, "rb") as f:
                creds = pickle.load(f)
            if isinstance(creds, Credentials):
                return creds
        except Exception:
            pass

    return None


def authenticate_gmail():
    creds = _load_credentials_from_env() or _load_credentials_from_disk()

    if not creds or not creds.valid:
        if not CREDENTIALS_JSON_PATH.exists():
            raise FileNotFoundError(
                f"Missing Gmail credentials file: {CREDENTIALS_JSON_PATH}. "
                "Provide GMAIL_TOKEN_B64, token.json, token.pkl, or credentials.json."
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_JSON_PATH),
            SCOPES,
        )
        creds = flow.run_local_server(port=0)

        with open(TOKEN_JSON_PATH, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _build_html_body(text_body: str, tracking_pixel_url: str | None = None) -> str:
    paragraphs = []
    for line in (text_body or "").splitlines():
        line = line.rstrip()
        if not line.strip():
            paragraphs.append("<br>")
        else:
            paragraphs.append(f"<p>{escape(line)}</p>")

    html = [
        "<html>",
        '  <body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #111827;">',
    ]

    html.extend([f"    {p}" for p in paragraphs])

    if tracking_pixel_url:
        html.append(
            f"""
    <img
      src="{escape(tracking_pixel_url, quote=True)}"
      width="1"
      height="1"
      style="display:none !important; width:1px; height:1px; opacity:0; visibility:hidden;"
      alt=""
    />
"""
        )

    html.extend([
        "  </body>",
        "</html>",
    ])

    return "\n".join(html)


def send_email_gmail(
    to_email: str,
    subject: str,
    body: str,
    tracking_pixel_url: str | None = None,
    reply_to: str | None = None,
    html_body: str | None = None,
    thread_id: str | None = None,
) -> Dict[str, Any]:
    """
    Sends an email via Gmail API and returns send metadata.
    Returning thread_id/message_id is important for reply tracking.
    """
    service = authenticate_gmail()

    message = MIMEMultipart("alternative")
    message["To"] = to_email
    message["Subject"] = subject

    if FROM_EMAIL:
        message["From"] = FROM_EMAIL

    if reply_to:
        message["Reply-To"] = reply_to

    text_part = MIMEText(body or "", "plain", "utf-8")

    if html_body:
        final_html = html_body
        if tracking_pixel_url and tracking_pixel_url not in html_body:
            if "</body>" in html_body:
                final_html = html_body.replace(
                    "</body>",
                    f"""
    <img
      src="{escape(tracking_pixel_url, quote=True)}"
      width="1"
      height="1"
      style="display:none !important; width:1px; height:1px; opacity:0; visibility:hidden;"
      alt=""
    />
  </body>""",
                )
            else:
                final_html = html_body + _build_html_body("", tracking_pixel_url)
    else:
        final_html = _build_html_body(body or "", tracking_pixel_url)

    html_part = MIMEText(final_html, "html", "utf-8")

    message.attach(text_part)
    message.attach(html_part)

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

    body_payload: Dict[str, Any] = {"raw": raw_message}
    if thread_id:
        body_payload["threadId"] = thread_id

    send_message = service.users().messages().send(
        userId="me",
        body=body_payload,
    ).execute()

    result = {
        "success": True,
        "message_id": send_message.get("id"),
        "thread_id": send_message.get("threadId") or thread_id,
        "label_ids": send_message.get("labelIds", []),
        "raw_response": send_message,
    }

    print(
        f"✅ Gmail sent: message_id={result['message_id']} "
        f"thread_id={result['thread_id']}"
    )
    return result


def send_via_gmail(
    to_email: str,
    subject: str,
    body: str,
    tracking_pixel_url: str | None = None,
    reply_to: str | None = None,
    html_body: str | None = None,
    thread_id: str | None = None,
) -> Dict[str, Any]:
    return send_email_gmail(
        to_email=to_email,
        subject=subject,
        body=body,
        tracking_pixel_url=tracking_pixel_url,
        reply_to=reply_to,
        html_body=html_body,
        thread_id=thread_id,
    )