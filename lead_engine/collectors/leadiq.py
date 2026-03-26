# collectors/leadiq.py

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
        "linkedin": lead.get("linkedin"),
        "company": lead.get("company"),
        "website": lead.get("website"),
        "country": lead.get("country"),
        "source": "LeadIQ",

        # System fields
        "status": "enriched",
        "deal_value": lead.get("deal_value", 0),
        "engagement_score": lead.get("engagement_score", 0),
        "priority_score": lead.get("priority_score", 0),

        # Enrichment strength
        "channel_strength": lead.get("channel_strength", 0),
        "confidence_score": lead.get("confidence_score", 0.6),
        "initial_score": lead.get("initial_score", 3)
    }


# ---------------------------------------------------
# Generate LinkedIn URL (Mock / Expandable)
# ---------------------------------------------------
def generate_linkedin(name: str, company: str) -> str:
    if not name or not company:
        return None

    clean_name = name.lower().replace(" ", "-")
    clean_company = company.lower().replace(" ", "")

    return f"https://linkedin.com/in/{clean_name}-{clean_company}"


# ---------------------------------------------------
# Generate Phone (Mock)
# ---------------------------------------------------
def generate_phone() -> str:
    return "+1-202-555-" + str(1000 + int(time.time()) % 9000)


# ---------------------------------------------------
# Channel Strength Score
# ---------------------------------------------------
def calculate_channel_strength(lead: Dict) -> int:
    score = 0

    if lead.get("email"):
        score += 2
    if lead.get("linkedin"):
        score += 2
    if lead.get("phone"):
        score += 3

    return score


# ---------------------------------------------------
# Confidence Score
# ---------------------------------------------------
def calculate_confidence(lead: Dict) -> float:
    score = 0.5

    if lead.get("email"):
        score += 0.2
    if lead.get("linkedin"):
        score += 0.2
    if lead.get("phone"):
        score += 0.2

    return min(score, 1.0)


# ---------------------------------------------------
# Score Boost
# ---------------------------------------------------
def boost_score(lead: Dict) -> int:
    score = lead.get("initial_score", 3)

    if lead.get("channel_strength", 0) >= 4:
        score += 3

    if lead.get("confidence_score", 0) > 0.7:
        score += 2

    return score


# ---------------------------------------------------
# Main Enrichment Function
# ---------------------------------------------------
@timer("LeadIQ Enrichment")
@retry
async def fetch_leadiq_leads(company_name: str = None) -> List[Dict]:
    """
    Multi-channel enrichment layer:
    - Adds LinkedIn + Phone
    - Calculates channel strength
    - Improves outreach success rate
    """

    if not company_name:
        return []

    if not check_quota("leadiq"):
        print("⚠️ LeadIQ quota exceeded")
        return []

    start_time = time.perf_counter()

    try:
        await asyncio.sleep(0)

        leads = []
        base_name = company_name.split()[0]
        website = f"https://{company_name.lower().replace(' ', '')}.com"

        roles = ["Founder", "CEO", "Operations Manager"]

        for title in roles:
            name = f"{title} {base_name}"

            linkedin = generate_linkedin(name, company_name)
            phone = generate_phone()

            raw_lead = {
                "name": name,
                "title": title,
                "email": None,  # usually added by other enrichers
                "phone": phone,
                "linkedin": linkedin,
                "company": company_name,
                "website": website,
                "country": "United States"
            }

            raw_lead["channel_strength"] = calculate_channel_strength(raw_lead)
            raw_lead["confidence_score"] = calculate_confidence(raw_lead)
            raw_lead["initial_score"] = boost_score(raw_lead)

            leads.append(normalize_lead(raw_lead))

        duration = round(time.perf_counter() - start_time, 2)

        print(f"🚀 LeadIQ enriched {company_name}: {len(leads)} leads | {duration}s")

        return leads

    except Exception as e:
        print(f"❌ LeadIQ enrichment error: {e}")
        return []