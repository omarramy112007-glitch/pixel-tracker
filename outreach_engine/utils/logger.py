# outreach_engine/utils/logger.py

import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """
    Returns a configured logger instance.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "[%(levelname)s] %(message)s | %(asctime)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def log_info(logger: logging.Logger, message: str, **kwargs):
    details = " ".join([f"{k}={v}" for k, v in kwargs.items()])
    logger.info(f"{message} | {details}" if details else message)


def log_warning(logger: logging.Logger, message: str, **kwargs):
    details = " ".join([f"{k}={v}" for k, v in kwargs.items()])
    logger.warning(f"{message} | {details}" if details else message)


def log_error(logger: logging.Logger, message: str, **kwargs):
    details = " ".join([f"{k}={v}" for k, v in kwargs.items()])
    logger.error(f"{message} | {details}" if details else message)


logger = get_logger("outreach_engine")