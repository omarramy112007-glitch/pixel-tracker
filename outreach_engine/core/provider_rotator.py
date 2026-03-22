# outreach_engine/core/provider_rotator.py

from core.provider_config import PROVIDERS


def get_available_provider():
    """
    Returns best provider based on priority + remaining quota
    """

    sorted_providers = sorted(
        PROVIDERS.items(),
        key=lambda x: x[1]["priority"]
    )

    for name, config in sorted_providers:
        if config["sent_today"] < config["daily_limit"]:
            return name

    return None


def increment_provider_usage(provider: str):
    if provider in PROVIDERS:
        PROVIDERS[provider]["sent_today"] += 1