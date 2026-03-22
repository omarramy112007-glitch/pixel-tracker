# File: outreach_engine/alerts/alert_dispatcher.py

from typing import Dict
import smtplib
import requests
from slack_sdk import WebClient

# -----------------------------
# CONFIGURATION
# -----------------------------
SMTP_SERVER = "smtp.example.com"
SMTP_PORT = 587
SMTP_USER = "alerts@example.com"
SMTP_PASS = "your_password"

SLACK_TOKEN = "xoxb-your-slack-token"
SLACK_CHANNEL = "#campaign-alerts"

# -----------------------------
# Send Email Alert
# -----------------------------
def send_email_alert(to_email: str, subject: str, body: str):
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            msg = f"Subject: {subject}\n\n{body}"
            server.sendmail(SMTP_USER, to_email, msg)
        print(f"✅ Email alert sent to {to_email}")
    except Exception as e:
        print(f"❌ Failed to send email alert: {e}")


# -----------------------------
# Send Slack Alert
# -----------------------------
def send_slack_alert(message: str):
    try:
        client = WebClient(token=SLACK_TOKEN)
        client.chat_postMessage(channel=SLACK_CHANNEL, text=message)
        print("✅ Slack alert sent")
    except Exception as e:
        print(f"❌ Failed to send Slack alert: {e}")


# -----------------------------
# Send Webhook Alert
# -----------------------------
def send_webhook_alert(url: str, payload: Dict):
    try:
        requests.post(url, json=payload, timeout=5)
        print(f"✅ Webhook alert sent to {url}")
    except Exception as e:
        print(f"❌ Failed to send webhook alert: {e}")


# -----------------------------
# Generic Trigger
# -----------------------------
def trigger_alert(event_name: str, campaign_id: int, details: str):
    """
    Central alert function.
    Can send via email, Slack, webhook simultaneously.
    """
    subject = f"[ALERT] {event_name} (Campaign {campaign_id})"
    body = f"Campaign {campaign_id} triggered alert: {event_name}\nDetails:\n{details}"

    # Example recipients
    send_email_alert("admin@example.com", subject, body)
    send_slack_alert(f"{subject}\n{details}")
    # send_webhook_alert("https://hooks.example.com/campaign", {"event": event_name, "campaign_id": campaign_id, "details": details})