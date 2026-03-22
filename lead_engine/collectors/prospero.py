# collectors/prospero.py

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
        "source": "Prospero",

        # System fields
        "status": "enriched",
        "deal_value": lead.get("deal_value", 0),
        "engagement_score": lead.get("engagement_score", 0),
        "priority_score": lead.get("priority_score", 0),

        # High-ticket signals
        "intent_score": lead.get("intent_score", 0),
        "company_size_score": lead.get("company_size_score", 1),
        "confidence_score": lead.get("confidence_score", 0.6),
        "initial_score": lead.get("initial_score", 4)
    }


# ---------------------------------------------------
# Estimate Company Size (Mock Logic)
# ---------------------------------------------------
def estimate_company_size(company_name: str) -> int:
    """
    Simple heuristic:
    bigger name → bigger company (placeholder)
    """
    length = len(company_name)

    if length > 15:
        return 3  # large
    elif length > 8:
        return 2  # medium
    return 1  # small


# ---------------------------------------------------
# Estimate Deal Value 🔥
# ---------------------------------------------------
def estimate_deal_value(title: str, company_size: int) -> float:
    base = 500

    title = (title or "").lower()

    if "ceo" in title or "founder" in title:
        base *= 3

    if company_size == 3:
        base *= 2
    elif company_size == 2:
        base *= 1.5

    return base


# ---------------------------------------------------
# Intent Score (Buyer Intent)
# ---------------------------------------------------
def calculate_intent(title: str) -> int:
    title = (title or "").lower()

    if title in ["ceo", "founder"]:
        return 5
    elif "marketing" in title:
        return 4
    elif "operations" in title:
        return 3

    return 2


# ---------------------------------------------------
# Confidence
# ---------------------------------------------------
def calculate_confidence(company_size: int, intent_score: int) -> float:
    score = 0.5

    if company_size >= 2:
        score += 0.2

    if intent_score >= 4:
        score += 0.2

    return min(score, 1.0)


# ---------------------------------------------------
# Score Boost
# ---------------------------------------------------
def boost_score(lead: Dict) -> int:
    score = lead.get("initial_score", 4)

    if lead.get("deal_value", 0) > 1000:
        score += 3

    if lead.get("intent_score", 0) >= 4:
        score += 2

    return score


# ---------------------------------------------------
# Main Enrichment Function
# ---------------------------------------------------
@timer("Prospero Enrichment")
@retry
async def fetch_prospero_leads(company_name: str = None) -> List[Dict]:
    """
    High-ticket enrichment:
    - Estimates deal value
    - Detects buyer intent
    - Prioritizes revenue-heavy leads
    """

    if not company_name:
        return []

    if not check_quota("prospero"):
        print("⚠️ Prospero quota exceeded")
        return []

    start_time = time.perf_counter()

    try:
        await asyncio.sleep(0)

        leads = []
        base_name = company_name.split()[0]
        website = f"https://{company_name.lower().replace(' ', '')}.com"

        roles = ["Founder", "CEO", "Marketing Director"]

        company_size = estimate_company_size(company_name)

        for title in roles:
            deal_value = estimate_deal_value(title, company_size)
            intent_score = calculate_intent(title)
            confidence = calculate_confidence(company_size, intent_score)

            raw_lead = {
                "name": f"{title} {base_name}",
                "title": title,
                "email": None,
                "phone": None,
                "company": company_name,
                "website": website,
                "country": "United States",

                # Revenue intelligence 🔥
                "deal_value": deal_value,
                "intent_score": intent_score,
                "company_size_score": company_size,
                "confidence_score": confidence
            }

            raw_lead["initial_score"] = boost_score(raw_lead)

            leads.append(normalize_lead(raw_lead))

        duration = round(time.perf_counter() - start_time, 2)

        print(f"🚀 Prospero enriched {company_name}: {len(leads)} leads | {duration}s")

        return leads

    except Exception as e:
        print(f"❌ Prospero enrichment error: {e}")
        return []