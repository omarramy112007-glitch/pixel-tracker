# outreach_engine/core/gmail_sender.py

from __future__ import annotations

import base64
import os
import re
import time

from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
)

from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any, Dict, Optional

import httpx

from outreach_engine.tracking.gmail_auth import authenticate

try:
    from google.auth.transport.requests import Request as GoogleRequest
except Exception:
    GoogleRequest = None


try:
    from outreach_engine.core.quota import (
        check_quota,
        get_wait_time,
        record_send,
        set_cooldown,
    )

except Exception:

    def check_quota(_provider: str) -> bool:
        return True

    def get_wait_time(_provider: str) -> int:
        return 0

    def record_send(_provider: str) -> None:
        return None

    def set_cooldown(_provider: str, _seconds: int) -> None:
        return None


FROM_EMAIL = (
    os.getenv("GMAIL_FROM")
    or os.getenv("GMAIL_USER")
    or os.getenv("GMAIL_USER_EMAIL")
    or ""
)

GMAIL_TIMEOUT_SECONDS = float(os.getenv("GMAIL_TIMEOUT_SECONDS", "15"))
GMAIL_AUTH_TIMEOUT_SECONDS = float(os.getenv("GMAIL_AUTH_TIMEOUT_SECONDS", "15"))
GMAIL_API_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

MAX_RETRIES = max(1, int(os.getenv("GMAIL_SEND_MAX_RETRIES", "2")))
INITIAL_BACKOFF_SECONDS = max(1, int(os.getenv("GMAIL_SEND_INITIAL_BACKOFF_SECONDS", "3")))
MAX_BACKOFF_SECONDS = max(5, int(os.getenv("GMAIL_SEND_MAX_BACKOFF_SECONDS", "10")))

_AUTH_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gmail-auth")
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gmail-send")


class GmailRateLimitError(RuntimeError):
    def __init__(self, message: str, retry_after_seconds: Optional[int] = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _run_with_timeout(func, timeout_seconds: float, label: str, executor: ThreadPoolExecutor | None = None):
    executor = executor or _EXECUTOR
    future = executor.submit(func)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as e:
        future.cancel()
        raise TimeoutError(f"{label} timed out after {timeout_seconds:.1f}s") from e


def _get_creds():
    print("🔐 STEP 1: starting Gmail authenticate()")
    creds = _run_with_timeout(authenticate, GMAIL_AUTH_TIMEOUT_SECONDS, "gmail_auth", executor=_AUTH_EXECUTOR)
    print("🔐 STEP 2: authenticate() returned")
    return creds


def _get_access_token() -> str:
    creds = _get_creds()

    if creds is None:
        raise RuntimeError("authenticate() returned None")

    expired = bool(getattr(creds, "expired", False))
    valid = getattr(creds, "valid", None)
    refresh_token = getattr(creds, "refresh_token", None)
    token = getattr(creds, "token", None)

    print("🔐 STEP 3: token state")
    print(f"expired={expired}")
    print(f"valid={valid}")
    print(f"has_refresh_token={bool(refresh_token)}")
    print(f"has_token={bool(token)}")

    # Always force refresh if we have a refresh token
    # Handles cases where Google invalidates the token server-side
    # before the local expiry time
    if refresh_token:
        if GoogleRequest is None:
            raise RuntimeError("google-auth missing; cannot refresh Gmail token")

        def _do_refresh():
            creds.refresh(GoogleRequest())
            return creds

        try:
            refreshed = _run_with_timeout(
                _do_refresh,
                GMAIL_AUTH_TIMEOUT_SECONDS,
                "gmail_refresh",
                executor=_AUTH_EXECUTOR,
            )
            token = getattr(refreshed, "token", None)
            print("✅ Gmail token force-refreshed")
        except Exception as e:
            print(f"⚠ Token refresh failed: {e}, using existing token")

    if not token:
        raise RuntimeError("No Gmail access token available.")

    print("✅ STEP 4: token acquired")
    return token


def _build_mime_message(
    to_email: str,
    subject: str,
    body: str,
    tracking_pixel_url: str | None = None,
    reply_to: str | None = None,
    html_body: str | None = None,
) -> str:
    message = MIMEMultipart("alternative")
    message["To"] = to_email
    message["Subject"] = subject

    if FROM_EMAIL:
        message["From"] = FROM_EMAIL

    if reply_to:
        message["Reply-To"] = reply_to

    text_part = MIMEText(body or "", "plain", "utf-8")
    message.attach(text_part)

    final_html = html_body or f"""
    <html>
      <body style="font-family:Arial,sans-serif;">
        <p>{escape(body)}</p>
      </body>
    </html>
    """

    if tracking_pixel_url:
        final_html += f"""
        <img
            src="{escape(tracking_pixel_url, quote=True)}"
            width="1"
            height="1"
            style="display:none;"
        />
        """

    html_part = MIMEText(final_html, "html", "utf-8")
    message.attach(html_part)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return raw


def _post_gmail_send(
    token: str,
    raw_message: str,
    thread_id: str | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"raw": raw_message}

    if thread_id:
        payload["threadId"] = thread_id

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(connect=5.0, read=GMAIL_TIMEOUT_SECONDS, write=5.0, pool=5.0)

    def _do_post():
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            return client.post(GMAIL_API_SEND_URL, headers=headers, json=payload)

    response = _run_with_timeout(_do_post, GMAIL_TIMEOUT_SECONDS + 5, "gmail_send_http", executor=_EXECUTOR)

    print(f"📡 Gmail response: {response.status_code}")

    if response.status_code >= 400:
        raise RuntimeError(f"Gmail API error {response.status_code}: {response.text}")

    return response.json()


def send_email_gmail(
    to_email: str,
    subject: str,
    body: str,
    tracking_pixel_url: str | None = None,
    reply_to: str | None = None,
    html_body: str | None = None,
    thread_id: str | None = None,
) -> Dict[str, Any]:
    print(f"📨 Sending → {to_email}")

    raw_message = _build_mime_message(
        to_email=to_email,
        subject=subject,
        body=body,
        tracking_pixel_url=tracking_pixel_url,
        reply_to=reply_to,
        html_body=html_body,
    )

    last_error = None
    backoff = INITIAL_BACKOFF_SECONDS

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            token = _get_access_token()
            print("📨 Sending Gmail API request...")
            data = _post_gmail_send(token, raw_message, thread_id=thread_id)
            record_send("gmail")

            result = {
                "success": True,
                "message_id": data.get("id"),
                "thread_id": data.get("threadId"),
                "label_ids": data.get("labelIds", []),
                "raw_response": data,
            }

            print(f"✅ Gmail sent: message_id={result['message_id']}")
            return result

        except RuntimeError as e:
            last_error = e
            msg = str(e)

            # 401 means token is invalid — force refresh and retry
            if "401" in msg and attempt < MAX_RETRIES:
                print("⚠ Gmail 401 — forcing token refresh before retry...")
                try:
                    creds = _get_creds()
                    if GoogleRequest and getattr(creds, "refresh_token", None):
                        creds.refresh(GoogleRequest())
                        print("✅ Token refreshed after 401")
                except Exception as refresh_err:
                    print(f"⚠ Refresh after 401 failed: {refresh_err}")
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue

            # 429 rate limit
            if "429" in msg:
                raise GmailRateLimitError(
                    "Gmail rate limited.",
                    retry_after_seconds=60,
                ) from e

            if attempt < MAX_RETRIES:
                print(f"⚠ Gmail send failed: {e}. Retrying in {backoff}s...")
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue

            break

        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES:
                print(f"⚠ Gmail send failed: {e}. Retrying in {backoff}s...")
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue
            break

    raise RuntimeError(f"Gmail send failed: {last_error}")


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