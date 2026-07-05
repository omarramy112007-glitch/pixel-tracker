# outreach_engine/core/pixel_token.py

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Optional, Tuple

SIGNING_SECRET = os.getenv("PIXEL_SIGNING_SECRET", "").strip().encode("utf-8")

if not SIGNING_SECRET:
    raise RuntimeError(
        "PIXEL_SIGNING_SECRET is not set. Add a random 32+ character "
        "secret string to .env before starting the app."
    )


def _sign(payload: bytes) -> bytes:
    return hmac.new(SIGNING_SECRET, payload, hashlib.sha256).digest()[:8]


def encode_token(lead_id: int, campaign_id: int, email_type: str) -> str:
    """
    Packs lead_id:campaign_id:email_type into a short, signed, URL-safe
    token with no visible query parameters — nothing that looks like a
    tracking-parameter pattern (no campaign_id=, email_type=, ts=).
    """
    payload = f"{lead_id}:{campaign_id}:{email_type}".encode("utf-8")
    signature = _sign(payload)
    combined = payload + b"." + signature
    return base64.urlsafe_b64encode(combined).decode("utf-8").rstrip("=")


def decode_token(token: str) -> Optional[Tuple[int, int, str]]:
    """Reverses encode_token(). Returns None on malformed/tampered token."""
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode((token + padding).encode("utf-8"))
        payload, signature = raw.rsplit(b".", 1)

        if not hmac.compare_digest(signature, _sign(payload)):
            return None

        lead_id_str, campaign_id_str, email_type = payload.decode("utf-8").split(":", 2)
        return int(lead_id_str), int(campaign_id_str), email_type
    except Exception:
        return None
