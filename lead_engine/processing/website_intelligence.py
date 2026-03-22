# processing/website_intelligence.py

import aiohttp
from bs4 import BeautifulSoup

from core.cache import get_cache, set_cache
from core.retry import retry
from core.performance import timer


TECH_KEYWORDS = ["hubspot", "stripe", "zapier", "salesforce", "crm", "api"]
PAIN_KEYWORDS = ["manual", "inefficient", "slow", "time-consuming"]


@timer("Website Intelligence")
@retry
async def analyze_website(url: str) -> dict:

    if not url:
        return {"automation_score": 0, "tech_detected": [], "pain_signals": []}

    cache_key = f"website:{url}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    try:
        timeout = aiohttp.ClientTimeout(total=6)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:

                if resp.status != 200:
                    return {"automation_score": 0, "tech_detected": [], "pain_signals": []}

                html = await resp.text()

        text = BeautifulSoup(html, "html.parser").get_text().lower()

        tech_detected = [t for t in TECH_KEYWORDS if t in text]
        pain_signals = [p for p in PAIN_KEYWORDS if p in text]

        score = min(len(tech_detected) * 2, 10)

        result = {
            "automation_score": score,
            "tech_detected": tech_detected,
            "pain_signals": pain_signals
        }

        set_cache(cache_key, result)
        return result

    except Exception:
        return {"automation_score": 0, "tech_detected": [], "pain_signals": []}