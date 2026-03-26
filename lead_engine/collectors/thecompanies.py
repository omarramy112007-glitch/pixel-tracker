# collectors/thecompanies.py

import aiohttp
import asyncio
import time
from typing import List, Dict

from lead_engine.core.quota import check_quota
from lead_engine.core.retry import retry
from lead_engine.core.performance import timer


async def fetch_thecompanies_page(page: int = 1, limit: int = 10) -> List[Dict]:
    """
    Fetch one page from TheCompanies API
    """
    url = f"https://api.thecompaniesapi.com/v1/companies?page={page}&limit={limit}"
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            if response.status != 200:
                text = await response.text()
                raise Exception(f"TheCompanies error ({response.status}): {text}")
            data = await response.json()

    companies = data.get("companies", [])
    leads = []

    for c in companies:
        company_name = c.get("name") or "Unknown Company"
        website = c.get("website")
        country = c.get("country") or "United States"

        for title in ["Founder", "CEO"]:
            leads.append({
                "name": f"{title} {company_name.split()[0]}",
                "title": title,
                "email": None,
                "phone": None,
                "company": company_name,
                "website": website,
                "country": country,
                "source": "TheCompanies"
            })

    return leads


@timer("TheCompanies Collector")
@retry
async def fetch_thecompanies_leads(pages: int = 1, limit: int = 10) -> List[Dict]:
    """
    Async TheCompanies Collector
    - Handles multiple pages
    - Quota aware
    - Retry logic
    - Performance logging
    """
    if not check_quota("thecompanies"):
        print("⚠️ TheCompanies quota exceeded")
        return []

    start_time = time.perf_counter()

    try:
        tasks = [fetch_thecompanies_page(page=i, limit=limit) for i in range(1, pages + 1)]
        results = await asyncio.gather(*tasks)

        all_leads = [lead for page in results for lead in page]

        duration = round(time.perf_counter() - start_time, 2)
        print(f"🚀 TheCompanies collected: {len(all_leads)} leads | {duration}s")
        return all_leads

    except Exception as e:
        print(f"❌ TheCompanies request failed: {e}")
        return []