# core/quota.py

from datetime import datetime
from config import API_LIMITS

_usage = {api: 0 for api in API_LIMITS}
_last_reset = datetime.utcnow().date()


def _reset_if_new_day():
    global _usage, _last_reset

    today = datetime.utcnow().date()
    if today != _last_reset:
        _usage = {api: 0 for api in API_LIMITS}
        _last_reset = today


def check_quota(api_name: str) -> bool:
    _reset_if_new_day()

    if api_name not in API_LIMITS:
        print(f"⚠️ Unknown API: {api_name}")
        return False

    if _usage[api_name] >= API_LIMITS[api_name]:
        return False

    _usage[api_name] += 1
    return True


def get_usage(api_name: str) -> int:
    return _usage.get(api_name, 0)


def get_all_usage():
    return _usage