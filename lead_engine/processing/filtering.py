# processing/filtering.py

from lead_engine.core.performance import sync_timer

MARKETING_KEYWORDS = [
    "marketing", "digital marketing", "growth agency",
    "seo", "ppc", "lead generation", "performance marketing",
    "media buying", "social media agency"
]

ALLOWED_COUNTRIES = ["United States"]


@sync_timer("Filter Target Company")
def is_target_company(lead: dict) -> bool:
    """
    Target filter:
    - Must be US
    - Must have website
    - Must match marketing intent
    """

    if not lead:
        return False

    company = (lead.get("company") or "").lower()
    website = (lead.get("website") or "").lower()

    country = lead.get("country")
    if isinstance(country, dict):
        country = country.get("name")

    if country not in ALLOWED_COUNTRIES:
        return False

    if not website:
        return False

    return any(keyword in company or keyword in website for keyword in MARKETING_KEYWORDS)