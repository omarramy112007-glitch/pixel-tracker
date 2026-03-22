# collectors/leadgibbon.py

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
        "source": "LeadGibbon",

        # System fields
        "status": "enriched",
        "deal_value": lead.get("deal_value", 0),
        "engagement_score": lead.get("engagement_score", 0),
        "priority_score": lead.get("priority_score", 0),

        # Confidence score
        "confidence_score": lead.get("confidence_score", 0.5),
        "initial_score": lead.get("initial_score", 2)
    }


# ---------------------------------------------------
# Email Generator (Fallback)
# ---------------------------------------------------
def generate_email_patterns(name: str, company: str) -> List[str]:
    """
    Generate multiple email patterns
    """
    if not name or not company:
        return []

    parts = name.lower().split()
    if len(parts) < 2:
        return []

    first, last = parts[0], parts[-1]
    domain = company.lower().replace(" ", "") + ".com"

    return [
        f"{first}@{domain}",
        f"{first}.{last}@{domain}",
        f"{first[0]}{last}@{domain}",
        f"{first}{last[0]}@{domain}"
    ]


# ---------------------------------------------------
# Confidence + Scoring
# ---------------------------------------------------
def calculate_confidence(email: str, title: str) -> float:
    score = 0.5

    if email:
        score += 0.3

    title = (title or "").lower()
    if title in ["ceo", "founder"]:
        score += 0.2

    return min(score, 1.0)


def boost_score(lead: Dict) -> int:
    score = lead.get("initial_score", 2)

    if lead.get("email"):
        score += 4

    if lead.get("confidence_score", 0) > 0.7:
        score += 2

    return score


# ---------------------------------------------------
# Main Enrichment Function
# ---------------------------------------------------
@timer("LeadGibbon Enrichment")
@retry
async def fetch_leadgibbon_leads(company_name: str = None) -> List[Dict]:
    """
    Fallback enrichment layer:
    - Generates multiple email candidates
    - Adds confidence scoring
    - Used if other providers fail
    """

    if not company_name:
        return []

    if not check_quota("leadgibbon"):
        print("⚠️ LeadGibbon quota exceeded")
        return []

    start_time = time.perf_counter()

    try:
        await asyncio.sleep(0)

        leads = []
        base_name = company_name.split()[0]
        website = f"https://{company_name.lower().replace(' ', '')}.com"

        roles = ["CEO", "Founder", "Marketing Director"]

        for title in roles:
            name = f"{title} {base_name}"

            email_candidates = generate_email_patterns(name, company_name)

            # Pick best candidate (you can improve later)
            email = email_candidates[0] if email_candidates else None

            confidence = calculate_confidence(email, title)

            raw_lead = {
                "name": name,
                "title": title,
                "email": email,
                "phone": None,
                "company": company_name,
                "website": website,
                "country": "United States",
                "confidence_score": confidence,
                "initial_score": 2
            }

            raw_lead["initial_score"] = boost_score(raw_lead)

            leads.append(normalize_lead(raw_lead))

        duration = round(time.perf_counter() - start_time, 2)

        print(f"🚀 LeadGibbon enriched {company_name}: {len(leads)} leads | {duration}s")

        return leads

    except Exception as e:
        print(f"❌ LeadGibbon enrichment error: {e}")
        return []