# lead_engine/database/scoring.py

from database.analytics import calculate_performance

TITLE_WEIGHT_MAP = {}
INDUSTRY_WEIGHT_MAP = {}

DEFAULT_WEIGHT = 1.0


async def adjust_scoring_weights():
    global TITLE_WEIGHT_MAP, INDUSTRY_WEIGHT_MAP

    performance = await calculate_performance()

    title_stats = performance["title_stats"]
    industry_stats = performance["industry_stats"]

    # 🔥 Sort safely
    top_titles = sorted(title_stats.items(), key=lambda x: x[1].get("deal_rate", 0), reverse=True)[:5]
    top_industries = sorted(industry_stats.items(), key=lambda x: x[1].get("deal_rate", 0), reverse=True)[:5]

    # 🔥 Smooth scaling
    TITLE_WEIGHT_MAP = {t[0]: round(1 + t[1]["deal_rate"] * 2, 2) for t in top_titles}
    INDUSTRY_WEIGHT_MAP = {i[0]: round(1 + i[1]["deal_rate"] * 1.5, 2) for i in top_industries}

    return TITLE_WEIGHT_MAP, INDUSTRY_WEIGHT_MAP


def get_weight(title: str, industry: str):
    return TITLE_WEIGHT_MAP.get(title, DEFAULT_WEIGHT), INDUSTRY_WEIGHT_MAP.get(industry, DEFAULT_WEIGHT)