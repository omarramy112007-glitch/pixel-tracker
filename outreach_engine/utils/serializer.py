# outreach_engine/utils/serializer.py

from datetime import datetime

def serialize_data(data):
    if isinstance(data, dict):
        return {k: serialize_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [serialize_data(v) for v in data]
    elif isinstance(data, datetime):
        return data.isoformat()
    return data