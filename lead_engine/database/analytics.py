# database/analytics.py

from database.supabase_client import supabase
from core.cache import get_cache, set_cache

CACHE_KEY = "performance_stats"
CACHE_TTL_SECONDS = 300  # 5 min


def calculate_performance(limit: int = 5000):
    """
    Compute conversion rates with:
    - Pagination
    - Caching
    - Scalable aggregation
    """

    # 🔥 Cache first
    cached = get_cache(CACHE_KEY)
    if cached:
        return cached

    title_stats = {}
    industry_stats = {}

    page_size = 1000
    offset = 0

    while offset < limit:
        response = (
            supabase.table("leads")
            .select("title_category, industry, open_count, reply_count, meeting_count, deal_closed")
            .range(offset, offset + page_size - 1)
            .execute()
        )

        leads = response.data or []
        if not leads:
            break

        for lead in leads:
            title = lead.get("title_category") or "Unknown"
            industry = lead.get("industry") or "Unknown"

            # Init
            title_stats.setdefault(title, {"sent": 0, "open": 0, "reply": 0, "meeting": 0, "deal": 0})
            industry_stats.setdefault(industry, {"sent": 0, "open": 0, "reply": 0, "meeting": 0, "deal": 0})

            for stats, key in [(title_stats, title), (industry_stats, industry)]:
                stats[key]["sent"] += 1
                stats[key]["open"] += lead.get("open_count", 0)
                stats[key]["reply"] += lead.get("reply_count", 0)
                stats[key]["meeting"] += lead.get("meeting_count", 0)
                stats[key]["deal"] += int(lead.get("deal_closed", False))

        offset += page_size

    # 🔥 Compute rates
    for stats in [title_stats, industry_stats]:
        for val in stats.values():
            sent = val["sent"] or 1
            val["open_rate"] = val["open"] / sent
            val["reply_rate"] = val["reply"] / sent
            val["meeting_rate"] = val["meeting"] / sent
            val["deal_rate"] = val["deal"] / sent

    result = {"title_stats": title_stats, "industry_stats": industry_stats}

    # 🔥 Cache result
    set_cache(CACHE_KEY, result)

    return result