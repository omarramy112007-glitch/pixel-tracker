# outreach_engine/core/gmail_guard.py

import time
import threading

class GmailGlobalGuard:
    """
    Global single-source-of-truth for Gmail pacing.
    Prevents all sends across the app when Gmail is cooling down.
    """

    _lock = threading.Lock()
    _cooldown_until = 0.0
    _last_send = 0.0

    MIN_INTERVAL = 90  # default safe spacing between emails

    @classmethod
    def can_send(cls) -> bool:
        now = time.time()
        return now >= cls._cooldown_until

    @classmethod
    def wait_time(cls) -> int:
        now = time.time()
        if now < cls._cooldown_until:
            return int(cls._cooldown_until - now)

        elapsed = now - cls._last_send
        if elapsed < cls.MIN_INTERVAL:
            return int(cls.MIN_INTERVAL - elapsed)

        return 0

    @classmethod
    def block(cls, seconds: int, reason: str = ""):
        with cls._lock:
            cls._cooldown_until = max(cls._cooldown_until, time.time() + seconds)
            print(f"⛔ Gmail GLOBAL BLOCK for {seconds}s | reason: {reason}")

    @classmethod
    def mark_sent(cls):
        with cls._lock:
            cls._last_send = time.time()