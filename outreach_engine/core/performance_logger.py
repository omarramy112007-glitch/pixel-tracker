# File: outreach_engine/core/performance_logger.py

import time
import logging
import json

# Configure Python logging (can be replaced by ELK/Datadog/Splunk handlers)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def timer(metric_name: str):
    """
    Decorator to measure execution time, print color-coded console logs,
    and send structured logs to ELK/Datadog/Splunk.
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                end = time.time()
                elapsed = end - start

                # ---------------------------
                # Console log (color-coded)
                # ---------------------------
                color = "\033[92m"  # green
                if elapsed > 2.0:
                    color = "\033[93m"  # yellow
                if elapsed > 5.0:
                    color = "\033[91m"  # red

                print(f"{color}⏱ {func.__name__} took {elapsed:.2f}s [{metric_name}]\033[0m")

                # ---------------------------
                # Structured logging
                # ---------------------------
                log_payload = {
                    "function": func.__name__,
                    "duration": elapsed,
                    "metric_name": metric_name,
                    "args": args,
                    "kwargs": kwargs
                }

                # Send to logging backend (ELK/Datadog/Splunk)
                logging.info(json.dumps(log_payload, default=str))

        return wrapper
    return decorator