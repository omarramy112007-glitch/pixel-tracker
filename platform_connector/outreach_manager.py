# File: platform_connector/outreach_engine.py

from outreach_engine.main import run_initial_outreach, main as full_engine
from platform_connector.utils import retry

# -----------------------------
# Async (PRIMARY)
# -----------------------------
@retry(max_retries=2)
async def run_outreach(run_full: bool = False):
    """
    run_full = True → runs full autopilot (initial + follow-ups)
    run_full = False → runs initial outreach only
    """
    if run_full:
        return await full_engine()

    return await run_initial_outreach()


# -----------------------------
# Sync Wrapper (ONLY for scripts / CLI)
# -----------------------------
def run_outreach_sync(run_full: bool = False):
    import asyncio
    return asyncio.run(run_outreach(run_full))