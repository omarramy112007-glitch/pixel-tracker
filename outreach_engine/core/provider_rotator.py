# outreach_engine/core/provider_rotator.py

from __future__ import annotations

from threading import Lock
from typing import Optional

from outreach_engine.core.provider_config import PROVIDERS
from outreach_engine.core.quota import get_wait_time

_LOCK = Lock()


def _provider_priority(item) -> tuple:
    name, config = item
    priority = int(config.get("priority", 999))
    sent_today = int(config.get("sent_today", 0))
    daily_limit = int(config.get("daily_limit", 0))
    remaining = max(0, daily_limit - sent_today)

    wait_time = 0
    try:
        wait_time = int(get_wait_time(name))
    except Exception:
        wait_time = 0

    cooldown_penalty = 1 if wait_time > 0 else 0
    return (cooldown_penalty, priority, -remaining, name)


def get_available_provider(preferred: Optional[str] = None) -> Optional[str]:
    """
    Return the best provider based on priority and remaining quota.
    If `preferred` is supplied and still has quota + no cooldown, it is returned first.
    """
    with _LOCK:
        if preferred and preferred in PROVIDERS:
            cfg = PROVIDERS[preferred]
            sent_today = int(cfg.get("sent_today", 0))
            daily_limit = int(cfg.get("daily_limit", 0))
            if daily_limit <= 0 or sent_today < daily_limit:
                try:
                    if get_wait_time(preferred) == 0:
                        return preferred
                except Exception:
                    return preferred

        for name, config in sorted(PROVIDERS.items(), key=_provider_priority):
            daily_limit = int(config.get("daily_limit", 0))
            sent_today = int(config.get("sent_today", 0))

            try:
                if get_wait_time(name) > 0:
                    continue
            except Exception:
                pass

            if daily_limit > 0 and sent_today < daily_limit:
                return name

    return None


def increment_provider_usage(provider: str) -> None:
    with _LOCK:
        if provider not in PROVIDERS:
            return

        current = int(PROVIDERS[provider].get("sent_today", 0))
        daily_limit = int(PROVIDERS[provider].get("daily_limit", 0))

        if daily_limit > 0:
            PROVIDERS[provider]["sent_today"] = min(current + 1, daily_limit)
        else:
            PROVIDERS[provider]["sent_today"] = current + 1


def reset_provider_usage(provider: Optional[str] = None) -> None:
    with _LOCK:
        if provider:
            if provider in PROVIDERS:
                PROVIDERS[provider]["sent_today"] = 0
            return

        for name in PROVIDERS:
            PROVIDERS[name]["sent_today"] = 0