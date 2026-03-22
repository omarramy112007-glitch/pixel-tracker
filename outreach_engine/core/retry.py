# outreach_engine/core/retry.py

import asyncio
import functools

RETRY_LIMIT = 3


def retry(func):
    """
    Async retry decorator.

    Retries a coroutine up to RETRY_LIMIT times
    using exponential backoff.
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):

        for attempt in range(RETRY_LIMIT):

            try:
                return await func(*args, **kwargs)

            except Exception as e:

                if attempt == RETRY_LIMIT - 1:
                    print(f"❌ All retry attempts failed: {e}")
                    raise

                wait_time = 2 ** attempt

                print(
                    f"⚠ Retry {attempt + 1}/{RETRY_LIMIT} "
                    f"for {func.__name__} in {wait_time}s due to: {e}"
                )

                await asyncio.sleep(wait_time)

    return wrapper