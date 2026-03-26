# collectors/builtwith.py

import asyncio
import time
from typing import List, Dict

from lead_engine.core.quota import check_quota
from lead_engine.core.retry import retry
from lead_engine.core.performance import timer


# ---------------------------------------------------
# Normalize Lead
# ---------------------------------------------------
def normalize_lead(lead: Dict) -> Dict:
    return {
        "name": lead.get("name"),
        "title": lead.get("title"),
        "email": lead.get("email"),
        "phone": lead.get("phone"),
        "company": lead.get("company"),
        "website": lead.get("website"),
        "country": lead.get("country"),
        "source": "BuiltWith",

        # Core system fields
        "status": "new",
        "deal_value": 0,
        "engagement_score": 0,
        "priority_score": 0,

        # Early scoring
        "initial_score": lead.get("initial_score", 1)
    }


# ---------------------------------------------------
# Deduplication
# ---------------------------------------------------
def deduplicate(leads: List[Dict]) -> List[Dict]:
    seen = set()
    unique = []

    for l in leads:
        key = (l.get("company"), l.get("website"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(l)

    return unique


# ---------------------------------------------------
# Simulated BuiltWith Domain Fetch (Replace later with API)
# ---------------------------------------------------
async def fetch_domains() -> List[str]:
    """
    Replace this later with:
    - BuiltWith API
    - Scraping tech-based sites
    - Niche targeting
    """

    await asyncio.sleep(0)

    return [
        "growthlabs.io",
        "scalemarketing.ai",
        "leadgenius.co",
        "autofunnels.io",
        "marketboosters.com"
    ]


# ---------------------------------------------------
# Main Collector
# ---------------------------------------------------
@timer("BuiltWith Collector")
@retry
async def fetch_builtwith_leads() -> List[Dict]:
    """
    Ultra optimized BuiltWith collector:
    - Domain-based lead generation
    - Async ready
    - Normalized output
    - Initial scoring
    """

    if not check_quota("builtwith"):
        print("⚠️ BuiltWith quota exceeded")
        return []

    start_time = time.perf_counter()

    try:
        domains = await fetch_domains()
        leads = []

        for domain in domains:
            company_name = domain.split(".")[0].capitalize()

            # 🔥 Generate decision-makers per company
            for title in ["Founder", "CEO", "Head of Growth", "Marketing Director"]:

                # 🔥 Initial scoring
                initial_score = 1
                if title.lower() in ["ceo", "founder"]:
                    initial_score += 3
                elif "growth" in title.lower():
                    initial_score += 2

                raw_lead = {
                    "name": f"{title} {company_name}",
                    "title": title,
                    "email": None,  # filled later in enrichment
                    "phone": None,
                    "company": company_name,
                    "website": f"https://{domain}",
                    "country": "United States",
                    "initial_score": initial_score
                }

                leads.append(normalize_lead(raw_lead))

        # 🔥 Deduplicate
        leads = deduplicate(leads)

        duration = round(time.perf_counter() - start_time, 2)

        print(f"🚀 BuiltWith: {len(leads)} leads | {duration}s")
        return leads

    except Exception as e:
        print(f"❌ BuiltWith collector error: {e}")
        return []