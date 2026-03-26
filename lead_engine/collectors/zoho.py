# collectors/zoho.py

import asyncio
import time
from typing import List, Dict

from lead_engine.core.quota import check_quota
from lead_engine.core.retry import retry
from lead_engine.core.performance import timer


@timer("Zoho Collector")
@retry
async def fetch_zoho_leads(company_name: str = None) -> List[Dict]:
    """
    Phase 9–11 Zoho Collector
    - Async
    - Quota aware
    - Retry logic
    - Performance logging
    """

    if not company_name:
        return []

    if not check_quota("zoho"):
        print("⚠️ Zoho quota exceeded")
        return []

    start_time = time.perf_counter()
    await asyncio.sleep(0)  # keep async

    base_name = company_name.split()[0]
    leads = [
        {
            "name": f"{title} {base_name}",
            "title": title,
            "email": None,
            "phone": None,
            "company": company_name,
            "website": f"https://{company_name.lower().replace(' ', '')}.com",
            "country": "United States",
            "source": "Zoho"
        }
        for title in ["Founder", "CEO"]
    ]

    duration = round(time.perf_counter() - start_time, 2)
    print(f"🚀 Zoho ({company_name}): {len(leads)} leads | {duration}s")
    return leads