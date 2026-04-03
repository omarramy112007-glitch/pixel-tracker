# File: platform_connector/outreach_manager.py

from outreach_engine.main import run_initial_outreach, main as full_engine
from platform_connector.utils import retry, logger


@retry(max_retries=2)
async def run_outreach(run_full: bool = False):
    """
    run_full = True → runs full autopilot (initial + follow-ups)
    run_full = False → runs initial outreach only
    """
    logger.info(f"Starting outreach run | run_full={run_full}")

    try:
        if run_full:
            result = await full_engine()
        else:
            result = await run_initial_outreach()

        logger.info("Outreach run completed successfully")
        return result

    except Exception as e:
        logger.error(f"Outreach run failed: {e}")
        raise


def run_outreach_sync(run_full: bool = False):
    import asyncio
    return asyncio.run(run_outreach(run_full))