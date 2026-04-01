# outreach_engine/core/email_providers.py

import os
import base64
import smtplib
from email.mime.text import MIMEText
from typing import Optional, Callable, Dict

# Optional Gmail API deps
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except Exception:
    Credentials = None
    InstalledAppFlow = None
    build = None

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

SMTP_CONFIG = {
    "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
    "port": int(os.getenv("SMTP_PORT", "587")),
    "user": os.getenv("SMTP_USER", ""),
    "password": os.getenv("SMTP_PASSWORD", ""),
}

GMAIL_CREDENTIALS_FILE = os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json")
GMAIL_TOKEN_FILE = os.getenv("GMAIL_TOKEN_FILE", "token.json")

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
POSTMARK_API_KEY = os.getenv("POSTMARK_API_KEY", "")


# ---------------- Gmail API ----------------
def _authenticate_gmail():
    """
    OAuth-based Gmail API auth.
    Creates/refreshes token.json automatically.
    """
    if Credentials is None or InstalledAppFlow is None or build is None:
        raise RuntimeError(
            "Gmail API dependencies are missing. Install:\n"
            "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        )

    creds = None

    if os.path.exists(GMAIL_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if not os.path.exists(GMAIL_CREDENTIALS_FILE):
            raise FileNotFoundError(
                f"Missing {GMAIL_CREDENTIALS_FILE}. Put your Google OAuth client file next to this project."
            )

        flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)

        with open(GMAIL_TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def send_via_gmail_api(to_email: str, subject: str, body: str) -> bool:
    """
    Production-friendly free option.
    Uses the authenticated Gmail account directly.
    """
    service = _authenticate_gmail()

    message = MIMEText(body, _charset="utf-8")
    message["to"] = to_email
    message["subject"] = subject

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    service.users().messages().send(
        userId="me",
        body={"raw": raw_message}
    ).execute()

    print("✅ Sent via Gmail API")
    return True


# ---------------- SMTP ----------------
def send_via_smtp(to_email: str, subject: str, body: str) -> bool:
    """
    Legacy fallback.
    """
    if not SMTP_CONFIG["user"] or not SMTP_CONFIG["password"]:
        raise RuntimeError("SMTP credentials are missing in environment variables.")

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_CONFIG["user"]
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_CONFIG["host"], SMTP_CONFIG["port"]) as server:
        server.starttls()
        server.login(SMTP_CONFIG["user"], SMTP_CONFIG["password"])
        server.sendmail(SMTP_CONFIG["user"], to_email, msg.as_string())

    print("✅ Sent via SMTP")
    return True


# ---------------- SendGrid ----------------
def send_via_sendgrid(to_email: str, subject: str, body: str) -> bool:
    """
    Placeholder fallback. Keep it in the map, but Gmail API is preferred.
    """
    print("📤 SendGrid provider selected (placeholder)")
    raise RuntimeError("SendGrid provider is disabled in this setup.")


# ---------------- Postmark ----------------
def send_via_postmark(to_email: str, subject: str, body: str) -> bool:
    """
    Placeholder fallback. Keep it in the map, but Gmail API is preferred.
    """
    print("📤 Postmark provider selected (placeholder)")
    raise RuntimeError("Postmark provider is disabled in this setup.")


# ---------------- Provider Map ----------------
PROVIDER_MAP: Dict[str, Callable[[str, str, str], bool]] = {
    "gmail": send_via_gmail_api,
    "smtp": send_via_smtp,
    "sendgrid": send_via_sendgrid,
    "postmark": send_via_postmark,
}


# ---------------- Smart Fallback ----------------
def send_with_fallback(
    to_email: str,
    subject: str,
    body: str,
    preferred_provider: Optional[str] = None
) -> str:
    """
    Try Gmail API first by default.
    Returns the provider name that succeeded.
    """
    providers_order = ["gmail", "smtp", "sendgrid", "postmark"]

    if preferred_provider and preferred_provider in providers_order:
        providers_order.remove(preferred_provider)
        providers_order.insert(0, preferred_provider)

    last_error = None

    for provider_name in providers_order:
        try:
            print(f"🚀 Trying provider: {provider_name}")
            PROVIDER_MAP[provider_name](to_email, subject, body)
            print(f"✅ Sent via {provider_name}")
            return provider_name
        except Exception as e:
            print(f"❌ {provider_name} failed: {e}")
            last_error = e

    raise Exception(f"All providers failed: {last_error}")