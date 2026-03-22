# outreach_engine/utils/performance.py

import time
import functools
from datetime import datetime
from outreach_engine.database.supabase_client import supabase

PERFORMANCE_TABLE = "system_performance"

def timer(log_to_db: bool = True):
    """
    Decorator to measure execution time of functions and optionally log to database.

    Usage:
    @timer()
    def send_email():
        ...
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            result = func(*args, **kwargs)
            end_time = time.perf_counter()
            execution_time = end_time - start_time

            # Print console log with timestamp
            print(f"⏱ {func.__name__} took {execution_time:.4f}s | {datetime.utcnow().isoformat()}")

            # Record performance in database if enabled
            if log_to_db:
                try:
                    supabase.table(PERFORMANCE_TABLE).insert({
                        "function_name": func.__name__,
                        "execution_time": execution_time,
                        "timestamp": datetime.utcnow()
                    }).execute()
                except Exception as e:
                    print(f"⚠ Failed to log performance for {func.__name__}: {e}")

            return result

        return wrapper

    return decorator