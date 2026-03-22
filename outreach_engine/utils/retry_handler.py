# outreach_engine/utils/retry_handler.py

import time
from datetime import datetime
from typing import Callable

from outreach_engine.database.supabase_client import supabase
from outreach_engine.utils.logger import get_logger, log_error, log_warning

logger = get_logger(__name__)

FAILURE_TABLE = "system_failures"


# --------------------------------------------------
# Record Failure
# --------------------------------------------------

def record_failure(component: str, error_message: str, reason: str = ""):
    """
    Store a failure event in the database.
    """

    try:
        supabase.table(FAILURE_TABLE).insert({
            "component": component,
            "error_message": error_message,
            "failure_reason": reason,
            "retry_count": 0,
            "last_retry": datetime.utcnow().isoformat()
        }).execute()

    except Exception as db_error:
        log_error(logger, "Failed to record system failure", error=db_error)


# --------------------------------------------------
# Retry Logic
# --------------------------------------------------

def retry_operation(
    func: Callable,
    component: str,
    max_retries: int = 3,
    delay: float = 2.0,
    *args,
    **kwargs
):
    """
    Retry a failing operation.

    Parameters:
    func : function to retry
    component : name of system component
    max_retries : maximum retry attempts
    delay : seconds between retries
    """

    retry_count = 0

    while retry_count <= max_retries:

        try:
            return func(*args, **kwargs)

        except Exception as error:

            retry_count += 1

            log_warning(
                logger,
                "Retry attempt",
                component=component,
                retry=retry_count
            )

            if retry_count > max_retries:

                log_error(
                    logger,
                    "Max retries exceeded",
                    component=component,
                    error=str(error)
                )

                record_failure(
                    component=component,
                    error_message=str(error),
                    reason="max_retries_exceeded"
                )

                raise error

            time.sleep(delay)