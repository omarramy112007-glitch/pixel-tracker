# outreach_engine/core/retry.py

import asyncio
import inspect
import functools
from typing import Callable, Any, Optional

from outreach_engine.utils.logger import get_logger

logger = get_logger(__name__)

RETRY_LIMIT = 3
DEFAULT_DELAY = 2


def retry(
    func: Optional[Callable] = None,
    *,
    max_retries: int = RETRY_LIMIT,
    delay: int = DEFAULT_DELAY,
):
    """
    Retry decorator for both sync and async functions.

    Supports:
    - @retry
    - @retry()
    - @retry(max_retries=3, delay=3)
    """
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
                        if attempt >= max_retries:
                            logger.error(f"All retry attempts failed for {target_func.__name__}: {e}")
                            raise
                        wait_time = delay * (2 ** (attempt - 1))
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
                    if attempt >= max_retries:
                        logger.error(f"All retry attempts failed for {target_func.__name__}: {e}")
                        raise
                    wait_time = delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"Retry {attempt}/{max_retries} for {target_func.__name__} "
                        f"in {wait_time}s due to: {e}"
                    )
                    # sync backoff
                    import time
                    time.sleep(wait_time)
            raise last_error  # safety fallback

        return sync_wrapper

    if func is not None and callable(func):
        return decorator(func)

    return decorator