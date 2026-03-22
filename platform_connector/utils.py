# File: platform_connector/utils.py
import asyncio, time
from functools import wraps
from datetime import datetime

# ---------------- Logging ----------------
import logging, json

def get_logger(name="platform_connector"):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("[%(levelname)s] %(message)s | %(asctime)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger

logger = get_logger()

# ---------------- Retry ----------------
def retry(max_retries=3, delay=2):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(1, max_retries+1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"Retry {attempt}/{max_retries} failed: {e}")
                    if attempt == max_retries:
                        raise
                    await asyncio.sleep(delay)
        return wrapper
    return decorator