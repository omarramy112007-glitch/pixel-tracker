# processing/cleaner.py

from lead_engine.core.retry import retry
from lead_engine.core.performance import sync_timer


@sync_timer("Clean Lead")
@retry
def clean_lead(raw: dict, source: str = "Unknown") -> dict:

    if not isinstance(raw, dict):
        return {}

    # -------- Country --------
    country = raw.get("country")
    if isinstance(country, dict):
        country = country.get("name")

    # -------- Normalize --------
    name = (raw.get("name") or "").strip()
    company = (raw.get("company") or "").strip()
    website = raw.get("website")

    # 🔥 INDUSTRY FIX (IMPORTANT)
    industry = raw.get("industry") or ""

    if not industry:
        if website:
            site = website.lower()

            if any(k in site for k in ["marketing", "agency", "seo", "growth"]):
                industry = "Marketing Agency"
            elif any(k in site for k in ["saas", "software", "app"]):
                industry = "SaaS"
            else:
                industry = "Unknown"

    return {
        "name": name or company or None,
        "email": raw.get("email"),
        "phone": raw.get("phone"),
        "company": company or None,
        "website": website,
        "source": raw.get("source") or source,
        "country": country,
        "industry": industry,   # ✅ FIXED
    }