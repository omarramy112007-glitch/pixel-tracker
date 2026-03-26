# processing/deduplication.py

from lead_engine.core.retry import retry
from lead_engine.core.performance import sync_timer


@sync_timer("Remove Duplicates")
@retry
def remove_duplicates(leads):
    """
    Smarter deduplication:
    Priority → email > website > company
    """

    seen = set()
    unique = []

    for lead in leads:
        identifier = (
            lead.get("email") or
            lead.get("website") or
            lead.get("company")
        )

        if not identifier:
            continue

        key = identifier.lower()

        if key not in seen:
            seen.add(key)
            unique.append(lead)

    return unique