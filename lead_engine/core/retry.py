# core/retry.py

import asyncio
from functools import wraps
from config import RETRY_LIMIT


def retry(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        for attempt in range(RETRY_LIMIT):
            try:
                return await func(*args, **kwargs)

            except Exception as e:
                if attempt == RETRY_LIMIT - 1:
                    print(f"❌ Final failure in {func.__name__}: {e}")
                    raise e

                wait_time = min(2 ** attempt, 10)  # cap at 10s
                print(f"[Retry] {func.__name__} failed ({attempt+1}). Retrying in {wait_time}s...")

                await asyncio.sleep(wait_time)

    return wrapper