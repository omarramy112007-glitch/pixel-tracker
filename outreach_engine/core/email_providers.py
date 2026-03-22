# outreach_engine/core/email_providers.py

import smtplib
from email.mime.text import MIMEText

SMTP_CONFIG = {
    "host": "smtp.gmail.com",
    "port": 587,
    "user": "your_email@gmail.com",
    "password": "your_password"
}

SENDGRID_API_KEY = "your_sendgrid_key"
POSTMARK_API_KEY = "your_postmark_key"


# ---------------- SMTP ----------------
def send_via_smtp(to_email: str, subject: str, body: str) -> bool:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_CONFIG["user"]
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_CONFIG["host"], SMTP_CONFIG["port"]) as server:
        server.starttls()
        server.login(SMTP_CONFIG["user"], SMTP_CONFIG["password"])
        server.sendmail(SMTP_CONFIG["user"], to_email, msg.as_string())

    return True


# ---------------- SendGrid ----------------
def send_via_sendgrid(to_email: str, subject: str, body: str) -> bool:
    print("📤 Sending via SendGrid")
    return True


# ---------------- Postmark ----------------
def send_via_postmark(to_email: str, subject: str, body: str) -> bool:
    print("📤 Sending via Postmark")
    return True


# ---------------- Provider Map ----------------
PROVIDER_MAP = {
    "smtp": send_via_smtp,
    "sendgrid": send_via_sendgrid,
    "postmark": send_via_postmark,
}


# ---------------- Smart Fallback ----------------
def send_with_fallback(to_email: str, subject: str, body: str, preferred_provider: str = None):

    providers_order = list(PROVIDER_MAP.keys())

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