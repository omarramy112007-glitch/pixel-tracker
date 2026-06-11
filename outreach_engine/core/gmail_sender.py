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
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

FROM_EMAIL = os.getenv("GMAIL_FROM") or os.getenv("GMAIL_USER")


def authenticate_gmail():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            "credentials.json", SCOPES
        )
        creds = flow.run_local_server(port=0)
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _pixel_tag_present(html: str) -> bool:
    """Check if a tracking pixel from our own server is already in the HTML."""
    return bool(re.search(r'/open/\d+\?', html, re.IGNORECASE))


def _build_html_body(text_body: str) -> str:
    """Convert plain text to minimal HTML — no pixel injection here."""
    paragraphs = []
    for line in (text_body or "").splitlines():
        line = line.rstrip()
        if not line.strip():
            paragraphs.append("<br>")
        else:
            paragraphs.append(f"<p>{escape(line)}</p>")

    return "\n".join([
        "<html>",
        '  <body style="font-family: Arial, sans-serif; font-size: 14px; '
        'line-height: 1.6; color: #111827;">',
        *[f"    {p}" for p in paragraphs],
        "  </body>",
        "</html>",
    ])


def send_email_gmail(
    to_email:           str,
    subject:            str,
    body:               str,
    tracking_pixel_url: Optional[str] = None,
    reply_to:           Optional[str] = None,
    html_body:          Optional[str] = None,
) -> dict:
    service = authenticate_gmail()

    message           = MIMEMultipart("alternative")
    message["To"]     = to_email
    message["Subject"] = subject
    if FROM_EMAIL:
        message["From"] = FROM_EMAIL
    if reply_to:
        message["Reply-To"] = reply_to

    text_part = MIMEText(body or "", "plain", "utf-8")

    if html_body:
        final_html = html_body

        # Only inject a pixel via tracking_pixel_url if the template
        # did NOT already embed one via {pixel_tag}.
        # outreach_sender embeds via {pixel_tag} and passes
        # tracking_pixel_url=None, so this block never fires for it.
        # Other callers that pass tracking_pixel_url directly are
        # handled here without stripping anything.
        if tracking_pixel_url and not _pixel_tag_present(final_html):
            pixel = (
                f'<img src="{escape(tracking_pixel_url, quote=True)}" '
                f'width="1" height="1" '
                f'style="display:none !important; opacity:0; visibility:hidden;" '
                f'alt="" />'
            )
            if "</body>" in final_html:
                final_html = final_html.replace("</body>", f"  {pixel}\n  </body>")
            else:
                final_html = final_html + pixel
    else:
        # No html_body supplied — build from plain text and inject pixel
        final_html = _build_html_body(body or "")
        if tracking_pixel_url:
            pixel = (
                f'<img src="{escape(tracking_pixel_url, quote=True)}" '
                f'width="1" height="1" '
                f'style="display:none !important; opacity:0; visibility:hidden;" '
                f'alt="" />'
            )
            final_html = final_html.replace("</body>", f"  {pixel}\n  </body>")

    html_part = MIMEText(final_html, "html", "utf-8")

    message.attach(text_part)
    message.attach(html_part)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    result = service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()

    print(f"✅ Gmail sent: {result['id']} | thread: {result.get('threadId')}")
    return {
        "message_id": result["id"],
        "thread_id":  result.get("threadId"),
    }


def send_via_gmail(
    to_email:           str,
    subject:            str,
    body:               str,
    tracking_pixel_url: Optional[str] = None,
    reply_to:           Optional[str] = None,
    html_body:          Optional[str] = None,
) -> dict:
    return send_email_gmail(
        to_email=to_email,
        subject=subject,
        body=body,
        tracking_pixel_url=tracking_pixel_url,
        reply_to=reply_to,
        html_body=html_body,
    )
