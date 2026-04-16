# File: outreach_engine/ai/optimization_engine.py

import asyncio

from outreach_engine.core.retry import retry
from outreach_engine.core.performance import timer
from outreach_engine.analytics.campaign_analytics import get_campaign_funnel
from outreach_engine.analytics.metrics_calculator import get_metrics, calculate_rates
from outreach_engine.core.cache import get_cache, set_cache


@timer("Campaign Analysis")
@retry
async def analyze_campaign(campaign_id: int) -> dict:
    """
    Async Campaign Analysis with caching, retry & performance timer.
    Detects weak points and provides AI recommendations.
    """

    cache_key = f"campaign_analysis:{campaign_id}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    loop = asyncio.get_running_loop()
    metrics, funnel = await asyncio.gather(
        loop.run_in_executor(None, get_metrics, campaign_id),
        loop.run_in_executor(None, get_campaign_funnel, campaign_id),
    )

    rates = calculate_rates(metrics)

    insights = []
    actions = []

    # calculate_rates() returns percentages, so thresholds are percentages too
    if rates.get("open_rate", 0) < 20:
        insights.append("Low open rate detected")
        actions.append("Test new subject lines or sending times")

    if rates.get("click_rate", 0) < 10:
        insights.append("Low click rate")
        actions.append("Improve CTA or email body")

    if rates.get("reply_rate", 0) < 10:
        insights.append("Low reply rate")
        actions.append("Make message more personalized")

    if rates.get("conversion_rate", 0) < 5:
        insights.append("Low conversion rate")
        actions.append("Fix offer or landing page")

    if funnel.get("drop_off_to_reply_pct", 0) > 70:
        insights.append("Major drop-off before replies")
        actions.append("Rewrite first email")

    if funnel.get("drop_off_to_conversion_pct", 0) > 60:
        insights.append("Drop-off after replies")
        actions.append("Improve closing / CTA")

    result = {
        "campaign_id": campaign_id,
        "insights": insights,
        "recommended_actions": actions,
        "metrics": rates,
        "funnel": funnel,
    }

    set_cache(cache_key, result)
    return result