# collectors/getprospect.py

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
        "source": "GetProspect",

        # System fields
        "status": "enriched",
        "deal_value": lead.get("deal_value", 0),
        "engagement_score": lead.get("engagement_score", 0),
        "priority_score": lead.get("priority_score", 0),

        # Enrichment impact
        "initial_score": lead.get("initial_score", 2)
    }


# ---------------------------------------------------
# Fake Email Generator (replace with real API later)
# ---------------------------------------------------
def generate_email(name: str, company: str) -> str:
    """
    Placeholder logic:
    Replace with GetProspect API later
    """
    if not name or not company:
        return None

    first = name.split()[0].lower()
    domain = company.lower().replace(" ", "") + ".com"

    return f"{first}@{domain}"


# ---------------------------------------------------
# Score boost after enrichment
# ---------------------------------------------------
def boost_score(lead: Dict) -> int:
    score = lead.get("initial_score", 2)

    # 🔥 Email found = BIG boost
    if lead.get("email"):
        score += 5

    # 🔥 Important roles boost
    title = (lead.get("title") or "").lower()
    if title in ["ceo", "founder"]:
        score += 3
    elif "marketing" in title:
        score += 2

    return score


# ---------------------------------------------------
# Main Enrichment Function
# ---------------------------------------------------
@timer("GetProspect Enrichment")
@retry
async def fetch_getprospect_leads(company_name: str = None) -> List[Dict]:
    """
    Email enrichment layer:
    - Adds emails
    - Boosts lead score
    - Prepares for outreach
    """

    if not company_name:
        return []

    if not check_quota("getprospect"):
        print("⚠️ GetProspect quota exceeded")
        return []

    start_time = time.perf_counter()

    try:
        await asyncio.sleep(0)

        leads = []
        base_name = company_name.split()[0]
        website = f"https://{company_name.lower().replace(' ', '')}.com"

        roles = ["Founder", "CEO", "Marketing Director", "Head of Growth"]

        for title in roles:
            name = f"{title} {base_name}"

            raw_lead = {
                "name": name,
                "title": title,
                "email": generate_email(name, company_name),  # 🔥 enrichment
                "phone": None,
                "company": company_name,
                "website": website,
                "country": "United States",
                "initial_score": 2
            }

            # 🔥 Boost score after enrichment
            raw_lead["initial_score"] = boost_score(raw_lead)

            leads.append(normalize_lead(raw_lead))

        duration = round(time.perf_counter() - start_time, 2)

        print(f"🚀 GetProspect enriched {company_name}: {len(leads)} leads | {duration}s")

        return leads

    except Exception as e:
        print(f"❌ GetProspect enrichment error: {e}")
        return []