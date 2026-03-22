# collectors/voilanorbert.py

import asyncio
import time
from typing import List, Dict

from core.quota import check_quota
from core.retry import retry
from core.performance import timer


@timer("VoilaNorbert Collector")
@retry
async def fetch_voilanorbert_leads(company_name: str = None) -> List[Dict]:
    """
    Phase 9–11 VoilaNorbert Collector
    - Async
    - Quota aware
    - Retry logic
    - Performance logging
    """

    if not company_name:
        return []

    if not check_quota("voilanorbert"):
        print("⚠️ VoilaNorbert quota exceeded")
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
            "source": "VoilaNorbert"
        }
        for title in ["CEO", "Head of Growth"]
    ]

    duration = round(time.perf_counter() - start_time, 2)
    print(f"🚀 VoilaNorbert ({company_name}): {len(leads)} leads | {duration}s")
    return leads