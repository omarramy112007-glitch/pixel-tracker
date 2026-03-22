# processors/email_enrichment.py

import re
import asyncio
from core.cache import get_cache, set_cache
from core.retry import retry
from core.performance import timer

# ------------------------
# Email validation
# ------------------------

FREE_PROVIDERS = {
    "gmail.com", "yahoo.com", "outlook.com",
    "hotmail.com", "icloud.com"
}


def is_business_email(email):
    if not email:
        return False

    email = email.lower().strip()

    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return False

    domain = email.split("@")[1]
    return domain not in FREE_PROVIDERS


# ------------------------
# SAFE API IMPORTS
# ------------------------

EMAIL_APIS = []

# dynamically register only available APIs
def register_api(func):
    if callable(func):
        EMAIL_APIS.append(func)


# (add later when real APIs exist)
# register_api(prospero_api_lookup)
# register_api(getprospect_api_lookup)


# ------------------------
# Enrichment
# ------------------------

@timer("Email Enrichment")
@retry
async def enrich_email(person_name: str, company_domain: str) -> str | None:
    """
    Async + Cached + Safe fallback
    """

    if not person_name or not company_domain:
        return None

    cache_key = f"email:{person_name}:{company_domain}"
    cached = get_cache(cache_key)

    if cached:
        return cached

    for api_func in EMAIL_APIS:
        try:
            if asyncio.iscoroutinefunction(api_func):
                email = await api_func(person_name, company_domain)
            else:
                email = api_func(person_name, company_domain)

            if email and is_business_email(email):
                set_cache(cache_key, email)
                return email

        except Exception as e:
            print(f"⚠️ {api_func.__name__} failed: {e}")

    return None