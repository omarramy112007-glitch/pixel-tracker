# core/cache.py

import time
import asyncio
from typing import Any, Dict
from config import CACHE_TTL

_cache: Dict[str, tuple] = {}
_lock = asyncio.Lock()
MAX_CACHE_SIZE = 10000


async def get_cache(key: str) -> Any:
    async with _lock:
        item = _cache.get(key)

        if not item:
            return None

        data, timestamp = item

        if time.time() - timestamp > CACHE_TTL:
            del _cache[key]
            return None

        return data


async def set_cache(key: str, value: Any):
    async with _lock:
        if len(_cache) >= MAX_CACHE_SIZE:
            _cache.pop(next(iter(_cache)))  # remove oldest

        _cache[key] = (value, time.time())


async def clear_cache():
    async with _lock:
        _cache.clear()