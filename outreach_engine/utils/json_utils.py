# outreach_engine/utils/json_utils.py

import json
import datetime
from typing import Any


# --------------------------------------------------
# 🔥 Universal serializer
# --------------------------------------------------
def serialize(obj: Any):
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    return str(obj)


# --------------------------------------------------
# 🔥 Safe dumps (manual use if needed)
# --------------------------------------------------
def dumps(data: Any) -> str:
    return json.dumps(data, default=serialize)


# --------------------------------------------------
# 🔥 Convert ANY object to JSON-safe dict
# --------------------------------------------------
def safe_dict(data: Any):
    """
    Converts entire object to JSON-safe format (no datetime inside)
    """
    return json.loads(dumps(data))


# --------------------------------------------------
# 🔥 GLOBAL PATCH (IMPORTANT)
# --------------------------------------------------
_original_dumps = json.dumps


def patched_dumps(*args, **kwargs):
    if "default" not in kwargs:
        kwargs["default"] = serialize
    return _original_dumps(*args, **kwargs)


# Apply patch globally
json.dumps = patched_dumps