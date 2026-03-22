# File: platform_connector/cache.py

import time

CACHE = {}

def set_cache(key, value, ttl=300):
    CACHE[key] = {"value": value, "expires": time.time() + ttl}

def get_cache(key):
    data = CACHE.get(key)
    if not data: return None
    if time.time() > data["expires"]:
        CACHE.pop(key, None)
        return None
    return data["value"]