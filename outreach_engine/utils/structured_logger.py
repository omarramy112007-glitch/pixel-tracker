# outreach_engine/utils/structured_logger.py

import logging
import json
from datetime import datetime

class JsonFormatter(logging.Formatter):
    """
    Formats log records as structured JSON
    """
    def format(self, record):
        log_record = {
            "event": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # Include any extra fields
        if hasattr(record, "extra"):
            log_record.update(record.extra)

        return json.dumps(log_record)


def get_logger(name: str = "outreach_engine") -> logging.Logger:
    """
    Returns a JSON structured logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if called multiple times
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = JsonFormatter()
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger