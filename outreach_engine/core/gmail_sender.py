# outreach_engine/core/gmail_sender.py

from __future__ import annotations

import base64
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

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
            "credentials.json",
            SCOPES
        )
        creds = flow.run_local_server(port=0)

        with open("token.json", "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ✅ Strips existing 1x1 tracking pixels to prevent duplicate fires
def _strip_existing_pixels(html: str) -> str:
    pattern = r'<img[^>]*(?:width=["\']?1|height=["\']?1|display:none|\/open\/|\/track\/|tracking|pixel)[^>]*\/?>'
    return re.sub(pattern, '', html, flags=re.IGNORECASE | re.DOTALL)


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
        '  <body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #111827;">'
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
) -> bool:
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
        # ✅ Clean existing pixels first
        clean_html = _strip_existing_pixels(html_body)
        if tracking_pixel_url and tracking_pixel_url not in clean_html:
            final_html = clean_html.replace(
                "</body>",
                f"""
    <img
      src="{escape(tracking_pixel_url, quote=True)}"
      width="1"
      height="1"
      style="display:none !important; width:1px; height:1px; opacity:0; visibility:hidden;"
      alt=""
    />
  </body>"""
            )
        else:
            final_html = clean_html
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

    print("✅ Gmail sent:", send_message["id"])
    return True


def send_via_gmail(
    to_email: str,
    subject: str,
    body: str,
    tracking_pixel_url: str | None = None,
    reply_to: str | None = None,
    html_body: str | None = None,
) -> bool:
    return send_email_gmail(
        to_email=to_email,
        subject=subject,
        body=body,
        tracking_pixel_url=tracking_pixel_url,
        reply_to=reply_to,
        html_body=html_body,
    )
