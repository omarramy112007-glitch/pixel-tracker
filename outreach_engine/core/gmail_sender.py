# outreach_engine/core/gmail_sender.py

import base64
import os
from html import escape
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


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

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _build_html_body(body: str, tracking_pixel_url: str | None = None) -> str:
    safe_text = escape(body).replace("\n", "<br>")

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;">
        <div>{safe_text}</div>
    """

    if tracking_pixel_url:
        html += f"""
        <img
          src="{escape(tracking_pixel_url, quote=True)}"
          width="1"
          height="1"
          style="display:none !important; width:1px; height:1px; opacity:0; visibility:hidden;"
          alt=""
        />
        """

    html += """
      </body>
    </html>
    """
    return html


def send_email_gmail(
    to_email: str,
    subject: str,
    body: str,
    tracking_pixel_url: str | None = None,
    reply_to: str | None = None,
) -> bool:
    service = authenticate_gmail()

    message = MIMEMultipart("alternative")
    message["to"] = to_email
    message["subject"] = subject

    if reply_to:
        message["Reply-To"] = reply_to

    text_part = MIMEText(body, "plain", "utf-8")
    html_part = MIMEText(_build_html_body(body, tracking_pixel_url), "html", "utf-8")

    message.attach(text_part)
    message.attach(html_part)

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

    send_message = service.users().messages().send(
        userId="me",
        body={"raw": raw_message}
    ).execute()

    print("✅ Gmail sent:", send_message["id"])
    return True