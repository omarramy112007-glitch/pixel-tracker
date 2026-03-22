# outreach_engine/core/cache.py
import time

CACHE_TTL = 3600  # seconds
_cache = {}

def get_cache(key):
    value = _cache.get(key)
    if not value:
        return None
    data, timestamp = value
    if time.time() - timestamp > CACHE_TTL:
        del _cache[key]
        return None
    return data

def set_cache(key, data):
    _cache[key] = (data, time.time())