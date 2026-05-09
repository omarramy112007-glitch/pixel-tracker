# outreach_engine/core/retry.py

from __future__ import annotations

import asyncio
import functools
import inspect
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple, Type

try:
    from googleapiclient.errors import HttpError as GoogleHttpError
except Exception:
    GoogleHttpError = Exception  # type: ignore

from outreach_engine.utils.logger import get_logger

logger = get_logger(__name__)

RETRY_LIMIT = 3
DEFAULT_DELAY = 2
MAX_DELAY = 120

RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


def _extract_status(exc: BaseException) -> Optional[int]:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if status is not None:
        try:
            return int(status)
        except Exception:
            pass
    return None


def _extract_retry_after_seconds(exc: BaseException) -> Optional[int]:
    """
    Supports:
    - Retry-After response header
    - Google API error text with ISO timestamp
    """
    resp = getattr(exc, "resp", None)
    if resp is not None:
        for key in ("retry-after", "Retry-After"):
            retry_after = None
            try:
                headers = getattr(resp, "headers", None)
                if headers and hasattr(headers, "get"):
                    retry_after = headers.get(key)
                elif hasattr(resp, "get"):
                    retry_after = resp.get(key)
            except Exception:
                retry_after = None

            if retry_after:
                try:
                    return max(1, int(float(retry_after)))
                except Exception:
                    pass

    text = ""
    try:
        content = getattr(exc, "content", None)
        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="ignore")
        elif content is not None:
            text = str(content)
        else:
            text = str(exc)
    except Exception:
        text = str(exc)

    match = re.search(r"Retry after ([0-9T:\.\-\+Z]+)", text)
    if match:
        value = match.group(1)
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            seconds = int((dt - now).total_seconds())
            return max(1, seconds)
        except Exception:
            pass

    return None


def _is_retryable_exception(exc: BaseException) -> bool:
    status = _extract_status(exc)

    if status in RETRYABLE_HTTP_STATUSES:
        return True

    if isinstance(exc, GoogleHttpError):
        return True

    return False


def retry(
    func: Optional[Callable] = None,
    *,
    max_retries: int = RETRY_LIMIT,
    delay: int = DEFAULT_DELAY,
    max_delay: int = MAX_DELAY,
    retry_on: Optional[Tuple[Type[BaseException], ...]] = None,
    bail_on_long_retry_after: bool = True,
):
    """
    Retry decorator for sync and async functions.
    """
    def _should_retry(exc: BaseException) -> bool:
        if retry_on and isinstance(exc, retry_on):
            return True
        return _is_retryable_exception(exc)

    def decorator(target_func: Callable):
        if inspect.iscoroutinefunction(target_func):

            @functools.wraps(target_func)
            async def async_wrapper(*args, **kwargs):
                last_error = None
                for attempt in range(1, max_retries + 1):
                    try:
                        return await target_func(*args, **kwargs)
                    except Exception as e:
                        last_error = e
                        if not _should_retry(e) or attempt >= max_retries:
                            logger.error(
                                f"All retry attempts failed for {target_func.__name__}: {e}"
                            )
                            raise

                        retry_after = _extract_retry_after_seconds(e)
                        wait_time = retry_after if retry_after is not None else delay * (2 ** (attempt - 1))
                        wait_time = max(1, int(wait_time))

                        if retry_after is not None and retry_after > max_delay and bail_on_long_retry_after:
                            logger.warning(
                                f"Retry-After {retry_after}s exceeds max_delay={max_delay}s "
                                f"for {target_func.__name__}; stopping retries."
                            )
                            raise

                        wait_time = min(wait_time, max_delay)

                        logger.warning(
                            f"Retry {attempt}/{max_retries} for {target_func.__name__} "
                            f"in {wait_time}s due to: {e}"
                        )
                        await asyncio.sleep(wait_time)

                raise last_error  # safety fallback

            return async_wrapper

        @functools.wraps(target_func)
        def sync_wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(1, max_retries + 1):
                try:
                    return target_func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if not _should_retry(e) or attempt >= max_retries:
                        logger.error(
                            f"All retry attempts failed for {target_func.__name__}: {e}"
                        )
                        raise

                    retry_after = _extract_retry_after_seconds(e)
                    wait_time = retry_after if retry_after is not None else delay * (2 ** (attempt - 1))
                    wait_time = max(1, int(wait_time))

                    if retry_after is not None and retry_after > max_delay and bail_on_long_retry_after:
                        logger.warning(
                            f"Retry-After {retry_after}s exceeds max_delay={max_delay}s "
                            f"for {target_func.__name__}; stopping retries."
                        )
                        raise

                    wait_time = min(wait_time, max_delay)

                    logger.warning(
                        f"Retry {attempt}/{max_retries} for {target_func.__name__} "
                        f"in {wait_time}s due to: {e}"
                    )
                    time.sleep(wait_time)

            raise last_error  # safety fallback

        return sync_wrapper

    if func is not None and callable(func):
        return decorator(func)

    return decorator