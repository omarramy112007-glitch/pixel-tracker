# File: platform_connector/lead_manager.py

import asyncio
from platform_connector.utils import retry, logger
from platform_connector.cache import get_cache, set_cache

from lead_engine.main import async_collect_all

@retry(max_retries=3, delay=2)
async def fetch_leads():
    cached = get_cache("all_leads")
    if cached:
        logger.info("Returning cached leads")
        return cached

    leads = await async_collect_all()
    set_cache("all_leads", leads)
    return leads

def get_leads_sync():
    return asyncio.run(fetch_leads())