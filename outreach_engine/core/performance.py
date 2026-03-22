# outreach_engine/core/performance.py
import time

def timer(label: str):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            start = time.time()
            result = await func(*args, **kwargs)
            print(f"{label} took {time.time() - start:.2f}s")
            return result
        return wrapper
    return decorator