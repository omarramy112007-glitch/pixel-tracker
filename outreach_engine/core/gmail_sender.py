# outreach_engine/core/gmail_sender.py

from __future__ import annotations

import base64
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from outreach_engine.core.account_manager import (
    get_account_by_key,
    increment_sent_count,
)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

FROM_EMAIL = os.getenv("GMAIL_FROM") or os.getenv("GMAIL_USER")


def authenticate_gmail(account_key: Optional[str] = None):
    """
    If account_key is given, load that specific account's credentials
    from Supabase (multi-account mode). Otherwise fall back to the
    single legacy token.json flow for backward compatibility.

    Returns (service, from_email_address).
    """
    if account_key:
        account = get_account_by_key(account_key)
        if not account:
            raise RuntimeError(f"Sending account not found: {account_key}")
        creds = Credentials.from_authorized_user_info(
            account["_decoded_token"], scopes=SCOPES
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
        return build("gmail", "v1", credentials=creds), account.get("email_address")

    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            "credentials.json",
            SCOPES
        )
        creds = flow.run_local_server(port=0)

        with open("token.json", "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds), FROM_EMAIL


def _pixel_tag(tracking_pixel_url: str) -> str:
    return (
        f'<img src="{escape(tracking_pixel_url, quote=True)}" '
        f'width="1" height="1" '
        f'style="display:none !important; width:1px; height:1px; '
        f'opacity:0; visibility:hidden;" alt="" />'
    )


def _inject_tracking_pixel_into_html(
    html_body: str,
    tracking_pixel_url: str,
) -> str:
    if not tracking_pixel_url:
        return html_body

    if tracking_pixel_url in html_body:
        return html_body

    pixel = _pixel_tag(tracking_pixel_url)
    body_close = re.compile(r"</body\s*>", re.IGNORECASE)
    html_close = re.compile(r"</html\s*>", re.IGNORECASE)

    if body_close.search(html_body):
        return body_close.sub(lambda m: f"{pixel}\n{m.group(0)}", html_body, 1)

    if html_close.search(html_body):
        return html_close.sub(lambda m: f"{pixel}\n{m.group(0)}", html_body, 1)

    return f"{html_body}\n{pixel}"


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
        '  <body style="font-family: Arial, sans-serif; font-size: 14px; '
        'line-height: 1.6; color: #111827;">'
    ]

    html.extend([f"    {p}" for p in paragraphs])

    if tracking_pixel_url:
        html.append(_pixel_tag(tracking_pixel_url))

    html.extend([
        "  </body>",
        "</html>"
    ])

    return "\n".join(html)


def send_email_gmail(
    to_email: str,
    subject: str,
    body: str,
    tracking_pixel_url: str | None = None,
    reply_to: str | None = None,
    html_body: str | None = None,
    account_key: Optional[str] = None,
) -> dict | bool:
    """
    Returns dict with thread_id, message_id, and account_key on success,
    or False on failure. account_key is the sending account that was
    used, so the caller can store it on the lead for reply routing.
    """
    service, from_email = authenticate_gmail(account_key=account_key)

    message = MIMEMultipart("alternative")
    message["To"] = to_email
    message["Subject"] = subject

    if from_email:
        message["From"] = from_email

    if reply_to:
        message["Reply-To"] = reply_to

    text_part = MIMEText(body or "", "plain", "utf-8")

    if html_body:
        final_html = html_body
        if tracking_pixel_url:
            final_html = _inject_tracking_pixel_into_html(
                html_body=html_body,
                tracking_pixel_url=tracking_pixel_url,
            )
    else:
        final_html = _build_html_body(body or "", tracking_pixel_url)

    html_part = MIMEText(final_html, "html", "utf-8")

    message.attach(text_part)
    message.attach(html_part)

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

    send_message = service.users().messages().send(
        userId="me",
        body={"raw": raw_message}
    ).execute()

    msg_id = send_message.get("id", "")

    print(f"✅ Gmail sent ({account_key or 'default'}): {msg_id}")

    try:
        msg_data = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="metadata")
            .execute()
        )
        thread_id = msg_data.get("threadId", "")
    except Exception:
        thread_id = ""

    if account_key:
        increment_sent_count(account_key)

    return {
        "message_id":  msg_id,
        "thread_id":   thread_id,
        "account_key": account_key,
    }


def send_via_gmail(
    to_email: str,
    subject: str,
    body: str,
    tracking_pixel_url: str | None = None,
    reply_to: str | None = None,
    html_body: str | None = None,
    account_key: Optional[str] = None,
) -> dict | bool:
    return send_email_gmail(
        to_email=to_email,
        subject=subject,
        body=body,
        tracking_pixel_url=tracking_pixel_url,
        reply_to=reply_to,
        html_body=html_body,
        account_key=account_key,
    )
