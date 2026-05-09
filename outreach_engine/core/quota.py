# outreach_engine/core/quota.py

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dt_time, timezone
from threading import Lock
from typing import Dict, Optional

API_LIMITS = {
    "gmail": int(os.getenv("GMAIL_DAILY_LIMIT", "25")),
    "sendgrid": int(os.getenv("SENDGRID_DAILY_LIMIT", "1000")),
    "postmark": int(os.getenv("POSTMARK_DAILY_LIMIT", "500")),
    "smtp": int(os.getenv("SMTP_DAILY_LIMIT", "300")),
}

MIN_INTERVAL_SECONDS = {
    "gmail": int(os.getenv("GMAIL_MIN_INTERVAL_SECONDS", "120")),
    "sendgrid": int(os.getenv("SENDGRID_MIN_INTERVAL_SECONDS", "1")),
    "postmark": int(os.getenv("POSTMARK_MIN_INTERVAL_SECONDS", "1")),
    "smtp": int(os.getenv("SMTP_MIN_INTERVAL_SECONDS", "1")),
}

_LOCK = Lock()


@dataclass
class _QuotaState:
    count: int = 0
    last_send_at: Optional[datetime] = None
    reset_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _next_utc_midnight(now: Optional[datetime] = None) -> datetime:
    now = now or _now_utc()
    tomorrow_date = (now + timedelta(days=1)).date()
    return datetime.combine(tomorrow_date, dt_time.min, tzinfo=timezone.utc)


_state: Dict[str, _QuotaState] = {
    provider: _QuotaState(count=0, last_send_at=None, reset_at=_next_utc_midnight(), cooldown_until=None)
    for provider in API_LIMITS
}

usage = {k: 0 for k in API_LIMITS}


def _ensure_provider(provider: str) -> None:
    if provider not in API_LIMITS:
        raise ValueError(f"Unknown provider: {provider}")


def _reset_if_needed(provider: str) -> None:
    state = _state[provider]
    now = _now_utc()

    if state.reset_at is None or now >= state.reset_at:
        state.count = 0
        state.last_send_at = None
        state.cooldown_until = None
        state.reset_at = _next_utc_midnight(now)
        usage[provider] = 0


def set_cooldown(provider: str, seconds: int) -> None:
    _ensure_provider(provider)

    if seconds is None:
        return

    try:
        seconds = int(seconds)
    except Exception:
        return

    if seconds <= 0:
        return

    with _LOCK:
        _reset_if_needed(provider)
        state = _state[provider]
        until = _now_utc() + timedelta(seconds=seconds)

        if state.cooldown_until is None or until > state.cooldown_until:
            state.cooldown_until = until


def get_wait_time(provider: str) -> int:
    _ensure_provider(provider)

    with _LOCK:
        _reset_if_needed(provider)
        state = _state[provider]
        now = _now_utc()

        if state.cooldown_until is not None and now < state.cooldown_until:
            return max(1, int((state.cooldown_until - now).total_seconds()))

        if state.count >= API_LIMITS[provider]:
            if state.reset_at is None:
                return 60
            remaining = int((state.reset_at - now).total_seconds())
            return max(1, remaining)

        if state.last_send_at is not None:
            elapsed = (now - state.last_send_at).total_seconds()
            min_interval = MIN_INTERVAL_SECONDS.get(provider, 0)
            if elapsed < min_interval:
                return max(1, int(min_interval - elapsed))

        return 0


def check_quota(provider: str) -> bool:
    _ensure_provider(provider)

    with _LOCK:
        _reset_if_needed(provider)
        return get_wait_time(provider) == 0


def record_send(provider: str) -> None:
    _ensure_provider(provider)

    with _LOCK:
        _reset_if_needed(provider)
        state = _state[provider]
        state.count += 1
        state.last_send_at = _now_utc()
        state.cooldown_until = None
        usage[provider] = state.count


def get_usage(provider: str) -> int:
    _ensure_provider(provider)
    with _LOCK:
        _reset_if_needed(provider)
        return usage.get(provider, 0)


def reset_usage(provider: Optional[str] = None) -> None:
    with _LOCK:
        if provider is None:
            for p in API_LIMITS:
                _state[p] = _QuotaState(count=0, last_send_at=None, reset_at=_next_utc_midnight(), cooldown_until=None)
                usage[p] = 0
            return

        _ensure_provider(provider)
        _state[provider] = _QuotaState(count=0, last_send_at=None, reset_at=_next_utc_midnight(), cooldown_until=None)
        usage[provider] = 0