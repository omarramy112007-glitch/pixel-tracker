# core/performance.py

import time
from functools import wraps


def timer(label: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            result = await func(*args, **kwargs)
            duration = time.perf_counter() - start

            print(f"[Performance] {label} | {func.__name__} → {duration:.3f}s")
            return result

        return wrapper
    return decorator


def sync_timer(label: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            result = func(*args, **kwargs)
            duration = time.perf_counter() - start

            print(f"[Performance] {label} | {func.__name__} → {duration:.3f}s")
            return result

        return wrapper
    return decorator