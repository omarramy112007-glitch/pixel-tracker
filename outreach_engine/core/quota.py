# outreach_engine/core/quota.py

API_LIMITS = {
    "sendgrid": 1000,
    "postmark": 500,
    "smtp": 300,
}

# current usage
usage = {k: 0 for k in API_LIMITS}


def check_quota(provider: str) -> bool:
    """
    Check if the provider can still send emails.
    If quota available → increment usage.
    """

    if provider not in API_LIMITS:
        raise ValueError(f"Unknown provider: {provider}")

    if usage[provider] >= API_LIMITS[provider]:
        return False

    usage[provider] += 1
    return True


def get_usage(provider: str) -> int:
    return usage.get(provider, 0)