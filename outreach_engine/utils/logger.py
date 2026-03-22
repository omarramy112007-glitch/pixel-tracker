# outreach_engine/utils/logger.py

import logging
import sys


# --------------------------------------------------
# Logger Configuration
# --------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    """
    Returns a configured logger instance.

    Example:
        logger = get_logger(__name__)
        logger.info("Email sent", extra={"campaign": 12, "lead": 442})
    """

    logger = logging.getLogger(name)

    if logger.handlers:
        return logger  # Prevent duplicate handlers

    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)

    formatter = logging.Formatter(
        "[%(levelname)s] %(message)s | %(asctime)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    handler.setFormatter(formatter)

    logger.addHandler(handler)

    return logger


# --------------------------------------------------
# Structured Logging Helpers
# --------------------------------------------------

def log_info(logger: logging.Logger, message: str, **kwargs):
    """
    Log an INFO message with structured key=value data.
    """
    details = " ".join([f"{k}={v}" for k, v in kwargs.items()])
    logger.info(f"{message} | {details}")


def log_warning(logger: logging.Logger, message: str, **kwargs):
    """
    Log a WARNING message.
    """
    details = " ".join([f"{k}={v}" for k, v in kwargs.items()])
    logger.warning(f"{message} | {details}")


def log_error(logger: logging.Logger, message: str, **kwargs):
    """
    Log an ERROR message.
    """
    details = " ".join([f"{k}={v}" for k, v in kwargs.items()])
    logger.error(f"{message} | {details}")