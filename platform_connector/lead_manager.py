# File: platform_connector/lead_manager.py

import asyncio
from platform_connector.utils import retry, logger
from platform_connector.cache import get_cache, set_cache

# ✅ FIX: correct import
from lead_engine.collectors.main_collectors import collect_all_sources


@retry(max_retries=3, delay=2)
async def fetch_leads():
    # ✅ FIX: handle async cache correctly if needed
    cached = get_cache("all_leads")
    if cached:
        logger.info("Returning cached leads")
        return cached

    # ✅ FIX: correct function call
    leads = await collect_all_sources()

    # ✅ safety: always return list
    leads = leads or []

    set_cache("all_leads", leads)
    return leads


# ⚠️ SAFE SYNC WRAPPER
def get_leads_sync():
    try:
        return asyncio.run(fetch_leads())
    except RuntimeError:
        # If already inside event loop (FastAPI etc)
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(fetch_leads())