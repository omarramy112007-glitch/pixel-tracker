# outreach_engine/core/gmail_sender.py

from __future__ import annotations

import base64
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

try:
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials
    GOOGLE_LIBS_AVAILABLE = True
except Exception:
    GoogleRequest = None
    Credentials   = None
    GOOGLE_LIBS_AVAILABLE = False

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

FROM_EMAIL = (
    os.getenv("GMAIL_FROM")
    or os.getenv("GMAIL_USER")
    or os.getenv("GMAIL_USER_EMAIL")
    or ""
)

GMAIL_TIMEOUT_SECONDS      = float(os.getenv("GMAIL_TIMEOUT_SECONDS", "15"))
GMAIL_AUTH_TIMEOUT_SECONDS = float(os.getenv("GMAIL_AUTH_TIMEOUT_SECONDS", "15"))
GMAIL_API_SEND_URL         = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

MAX_RETRIES             = max(1, int(os.getenv("GMAIL_SEND_MAX_RETRIES", "2")))
INITIAL_BACKOFF_SECONDS = max(1, int(os.getenv("GMAIL_SEND_INITIAL_BACKOFF_SECONDS", "3")))
MAX_BACKOFF_SECONDS     = max(5, int(os.getenv("GMAIL_SEND_MAX_BACKOFF_SECONDS", "10")))

ROOT_DIR        = Path(__file__).resolve().parents[2]
TOKEN_JSON_PATH = Path(os.getenv("GMAIL_TOKEN_JSON_PATH", str(ROOT_DIR / "token.json")))

_AUTH_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gmail-auth")
_EXECUTOR      = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gmail-send")


class GmailRateLimitError(RuntimeError):
    def __init__(self, message: str, retry_after_seconds: Optional[int] = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _run_with_timeout(func, timeout_seconds: float, label: str, executor=None):
    ex     = executor or _EXECUTOR
    future = ex.submit(func)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as e:
        future.cancel()
        raise TimeoutError(f"{label} timed out after {timeout_seconds:.1f}s") from e


def _load_credentials():
    if not GOOGLE_LIBS_AVAILABLE or Credentials is None:
        raise RuntimeError("google-auth libraries not installed.")

    token_b64 = os.getenv("GMAIL_TOKEN_B64", "").strip()
    if token_b64:
        try:
            token_info = json.loads(base64.b64decode(token_b64).decode("utf-8"))
            creds = Credentials.from_authorized_user_info(token_info, scopes=SCOPES)
            print("✅ gmail_sender: loaded from GMAIL_TOKEN_B64")
            return creds
        except Exception as e:
            print(f"⚠ gmail_sender: GMAIL_TOKEN_B64 failed: {e}")

    if TOKEN_JSON_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_JSON_PATH), SCOPES)
            print(f"✅ gmail_sender: loaded from {TOKEN_JSON_PATH}")
            return creds
        except Exception as e:
            print(f"⚠ gmail_sender: token.json failed: {e}")

    raise RuntimeError(
        "No Gmail credentials found. "
        "Set GMAIL_TOKEN_B64 env var or place token.json in the project root."
    )


def _get_access_token() -> str:
    def _load_and_refresh():
        creds = _load_credentials()
        if (
            getattr(creds, "expired", False)
            and getattr(creds, "refresh_token", None)
            and GoogleRequest is not None
        ):
            try:
                creds.refresh(GoogleRequest())
                try:
                    TOKEN_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
                    TOKEN_JSON_PATH.write_text(creds.to_json(), encoding="utf-8")
                except Exception:
                    pass
                print("✅ gmail_sender: token refreshed")
            except Exception as e:
                print(f"⚠ gmail_sender: token refresh failed: {e}")
                raise RuntimeError(f"Token refresh failed: {e}") from e

        token = getattr(creds, "token", None)
        if not token:
            raise RuntimeError("No Gmail access token available.")
        return token

    return _run_with_timeout(
        _load_and_refresh,
        GMAIL_AUTH_TIMEOUT_SECONDS,
        "gmail_auth",
        executor=_AUTH_EXECUTOR,
    )


def _build_html_body(text_body: str) -> str:
    """
    Converts plain text to HTML.
    No pixel injection here — pixels are injected by outreach_sender._inject_pixel()
    before this function is ever called. gmail_sender never touches pixels.
    """
    paragraphs = []
    for line in (text_body or "").splitlines():
        line = line.rstrip()
        if not line.strip():
            paragraphs.append("<br>")
        else:
            paragraphs.append(f"<p>{escape(line)}</p>")

    return (
        "<html>"
        '<body style="font-family:Arial,sans-serif;font-size:14px;'
        'line-height:1.6;color:#111827;">'
        + "".join(paragraphs)
        + "</body></html>"
    )


def _build_mime_message(
    to_email: str,
    subject: str,
    body: str,
    reply_to: Optional[str] = None,
    html_body: Optional[str] = None,
) -> str:
    """
    Build the MIME message.

    tracking_pixel_url is intentionally removed from this function.
    The pixel is already embedded in html_body by outreach_sender._inject_pixel().
    gmail_sender must never inject pixels — it would create a second pixel
    causing double open counts in Supabase.

    If html_body is provided → use it as-is (pixel already inside).
    If html_body is None     → convert body text to HTML (no pixel, plain email).
    """
    message = MIMEMultipart("alternative")
    message["To"]      = to_email
    message["Subject"] = subject
    if FROM_EMAIL:
        message["From"] = FROM_EMAIL
    if reply_to:
        message["Reply-To"] = reply_to

    message.attach(MIMEText(body or "", "plain", "utf-8"))

    final_html = html_body if html_body else _build_html_body(body or "")
    message.attach(MIMEText(final_html, "html", "utf-8"))

    return base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")


def _post_gmail_send(
    token: str,
    raw_message: str,
    thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"raw": raw_message}
    if thread_id:
        payload["threadId"] = thread_id

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }

    timeout = httpx.Timeout(
        connect=5.0,
        read=GMAIL_TIMEOUT_SECONDS,
        write=5.0,
        pool=5.0,
    )

    def _do_post():
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            return client.post(GMAIL_API_SEND_URL, headers=headers, json=payload)

    response = _run_with_timeout(_do_post, GMAIL_TIMEOUT_SECONDS + 5, "gmail_send_http")

    print(f"📡 Gmail API response: {response.status_code}")

    if response.status_code == 429:
        raise GmailRateLimitError("Gmail rate limited.", retry_after_seconds=60)
    if response.status_code == 401:
        raise RuntimeError(f"401 Unauthorized: {response.text[:200]}")
    if response.status_code >= 400:
        raise RuntimeError(f"Gmail API error {response.status_code}: {response.text[:300]}")

    return response.json()


def send_email_gmail(
    to_email: str,
    subject: str,
    body: str,
    tracking_pixel_url: Optional[str] = None,
    reply_to: Optional[str] = None,
    html_body: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    tracking_pixel_url parameter kept for backwards compatibility only.
    It is completely ignored — pixels must be injected by the caller
    (outreach_sender._inject_pixel) before passing html_body here.
    Injecting here would create a second pixel = double open counts.
    """
    if tracking_pixel_url:
        print(
            f"⚠ gmail_sender: tracking_pixel_url ignored → "
            f"pixel must be pre-injected in html_body by outreach_sender"
        )

    print(f"📨 gmail_sender: sending to {to_email} | subject={subject[:60]!r}")

    raw_message = _build_mime_message(
        to_email=to_email,
        subject=subject,
        body=body,
        reply_to=reply_to,
        html_body=html_body,
        # No tracking_pixel_url — pixel already in html_body
    )

    last_error = None
    backoff    = INITIAL_BACKOFF_SECONDS

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            token = _get_access_token()
            data  = _post_gmail_send(token, raw_message, thread_id=thread_id)

            result = {
                "success":      True,
                "message_id":   data.get("id"),
                "thread_id":    data.get("threadId"),
                "label_ids":    data.get("labelIds", []),
                "raw_response": data,
            }
            print(f"✅ gmail_sender: sent message_id={result['message_id']}")
            return result

        except GmailRateLimitError:
            raise

        except RuntimeError as e:
            last_error = e
            if attempt < MAX_RETRIES:
                print(f"⚠ gmail_sender: attempt {attempt} failed: {e}, retrying in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue
            break

        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES:
                print(f"⚠ gmail_sender: attempt {attempt} unexpected: {e}, retrying in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue
            break

    raise RuntimeError(f"gmail_sender: all {MAX_RETRIES} attempts failed: {last_error}")


def send_via_gmail(
    to_email: str,
    subject: str,
    body: str,
    tracking_pixel_url: Optional[str] = None,
    reply_to: Optional[str] = None,
    html_body: Optional[str] = None,
    thread_id: Optional[str] = None,
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


def authenticate_gmail():
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError("google-auth libraries not installed.")
    from googleapiclient.discovery import build as _build
    creds = _load_credentials()
    if (
        getattr(creds, "expired", False)
        and getattr(creds, "refresh_token", None)
        and GoogleRequest is not None
    ):
        try:
            creds.refresh(GoogleRequest())
        except Exception:
            pass
    return _build("gmail", "v1", credentials=creds, cache_discovery=False)
