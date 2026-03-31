# file: outreach_engine/utils/json_utils.py

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from typing import Any


def serialize(obj: Any):
    """
    Safe JSON serializer for common non-serializable Python objects.
    Use with: json.dumps(data, default=serialize)
    """
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()

    if isinstance(obj, Decimal):
        return float(obj)

    if isinstance(obj, UUID):
        return str(obj)

    if isinstance(obj, Path):
        return str(obj)

    if isinstance(obj, set):
        return list(obj)

    return str(obj)