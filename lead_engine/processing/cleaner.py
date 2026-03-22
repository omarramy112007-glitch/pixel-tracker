# processing/cleaner.py

from core.retry import retry
from core.performance import sync_timer


@sync_timer("Clean Lead")
@retry
def clean_lead(raw: dict, source: str = "Unknown") -> dict:
    """
    Normalize a raw lead dict into standard format.
    - Safe extraction
    - Normalized fields
    - Prevent empty/invalid objects
    """

    if not isinstance(raw, dict):
        return {}

    # -------- Country safe extraction --------
    country = raw.get("country")
    if isinstance(country, dict):
        country = country.get("name")

    # -------- Normalize --------
    name = (raw.get("name") or "").strip()
    company = (raw.get("company") or "").strip()

    return {
        "name": name or company or None,
        "email": raw.get("email"),
        "phone": raw.get("phone"),
        "company": company or None,
        "website": raw.get("website"),
        "source": raw.get("source") or source,
        "country": country
    }