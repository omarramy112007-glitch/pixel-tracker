# collectors/explorium.py

import asyncio
import time
from typing import List, Dict

from core.quota import check_quota
from core.retry import retry
from core.performance import timer


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
        "source": "Explorium",

        # System fields
        "status": "enriched",
        "deal_value": 0,
        "engagement_score": 0,
        "priority_score": 0,

        # Enrichment scoring
        "initial_score": lead.get("initial_score", 2)
    }


# ---------------------------------------------------
# Smart Role Selection (based on company type later)
# ---------------------------------------------------
def get_target_roles(company_name: str) -> List[str]:
    """
    You can later plug AI classification here:
    SaaS → Growth roles
    Enterprise → C-level
    """
    return ["Founder", "CEO", "Head of Growth", "Marketing Director"]


# ---------------------------------------------------
# Main Enrichment Function
# ---------------------------------------------------
@timer("Explorium Enrichment")
@retry
async def enrich_company(company_name: str = None) -> List[Dict]:
    """
    Ultra enrichment layer:
    - Expands company into multiple decision-makers
    - Adds scoring
    - Prepares for outreach + ML
    """

    if not company_name:
        return []

    if not check_quota("explorium"):
        print("⚠️ Explorium quota exceeded")
        return []

    start_time = time.perf_counter()

    try:
        await asyncio.sleep(0)

        leads = []
        base_name = company_name.split()[0]
        website = f"https://{company_name.lower().replace(' ', '')}.com"

        roles = get_target_roles(company_name)

        for title in roles:

            # 🔥 Intelligent scoring
            initial_score = 2
            if title.lower() in ["ceo", "founder"]:
                initial_score += 4
            elif "growth" in title.lower():
                initial_score += 3
            elif "marketing" in title.lower():
                initial_score += 2

            raw_lead = {
                "name": f"{title} {base_name}",
                "title": title,
                "email": None,  # filled later by enrichment APIs
                "phone": None,
                "company": company_name,
                "website": website,
                "country": "United States",
                "initial_score": initial_score
            }

            leads.append(normalize_lead(raw_lead))

        duration = round(time.perf_counter() - start_time, 2)

        print(f"🚀 Explorium enriched {company_name}: {len(leads)} leads | {duration}s")

        return leads

    except Exception as e:
        print(f"❌ Explorium enrichment error: {e}")
        return []