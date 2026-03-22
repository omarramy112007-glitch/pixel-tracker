# collectors/serpapi.py

import asyncio
import time
from typing import List, Dict

from core.quota import check_quota
from core.retry import retry
from core.performance import timer


async def fetch_serpapi_page(query: str, page: int = 1, per_page: int = 10) -> List[Dict]:
    """
    Fetch one page of SERPAPI results (simulated).
    """
    await asyncio.sleep(0)  # keep async

    # Mocked results for demonstration
    sample_results = [
        {"company": f"Company{page}A", "website": f"https://company{page}a.com", "country": "United States"},
        {"company": f"Company{page}B", "website": f"https://company{page}b.com", "country": "United States"},
    ]

    leads = []
    for result in sample_results:
        company_name = result.get("company")
        website = result.get("website")
        country = result.get("country") or "United States"

        for title in ["Founder", "Marketing Director"]:
            leads.append({
                "name": f"{title} {company_name.split()[0]}",
                "title": title,
                "email": None,
                "phone": None,
                "company": company_name,
                "website": website,
                "country": country,
                "source": "SERPAPI",
            })

    return leads


@timer("SERPAPI Collector")
@retry
async def fetch_serpapi_leads(query: str = "marketing agencies USA", pages: int = 2, per_page: int = 10) -> List[Dict]:
    """
    Async SERPAPI Collector
    - Handles multiple pages
    - Quota aware
    - Retry logic
    - Performance logging
    """
    if not check_quota("serpapi"):
        print(f"⚠️ SERPAPI quota exceeded for query: {query}")
        return []

    start_time = time.perf_counter()

    try:
        tasks = [fetch_serpapi_page(query, page=i, per_page=per_page) for i in range(1, pages + 1)]
        results = await asyncio.gather(*tasks)

        # Flatten the results
        all_leads = [lead for page in results for lead in page]

        duration = round(time.perf_counter() - start_time, 2)
        print(f"🚀 SERPAPI ({query}): {len(all_leads)} leads | {duration}s")

        return all_leads

    except Exception as e:
        print(f"❌ SERPAPI error ({query}): {e}")
        return []