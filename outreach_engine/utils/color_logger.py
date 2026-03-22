# outreach_engine/utils/color_logger.py

import logging

# ANSI color codes
COLOR_CODES = {
    "GREEN": "\033[92m",
    "YELLOW": "\033[93m",
    "RED": "\033[91m",
    "BLUE": "\033[94m",
    "RESET": "\033[0m"
}

EVENT_COLORS = {
    "email_sent": COLOR_CODES["GREEN"],
    "retry_attempt": COLOR_CODES["YELLOW"],
    "smtp_failure": COLOR_CODES["RED"],
    "analytics_updated": COLOR_CODES["BLUE"],
    "conversion": COLOR_CODES["GREEN"],
}

class ColorFormatter(logging.Formatter):
    """
    Logs messages with color based on event type.
    """
    def format(self, record):
        color = EVENT_COLORS.get(record.name.lower(), COLOR_CODES["RESET"])
        message = super().format(record)
        return f"{color}{message}{COLOR_CODES['RESET']}"

def get_color_logger(name: str = "outreach_engine") -> logging.Logger:
    """
    Returns a color-coded console logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = ColorFormatter("%(name)s | %(levelname)s | %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger