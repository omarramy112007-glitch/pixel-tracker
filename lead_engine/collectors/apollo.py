# collectors/apollo.py

import os
import aiohttp
import asyncio
from dotenv import load_dotenv

from core.quota import check_quota
from core.proxy_manager import get_proxy
from core.retry import retry
from core.performance import timer

load_dotenv()
API_KEY = os.getenv("APOLLO_API_KEY")

BASE_URL = "https://api.apollo.io/v1/mixed_people/search"


@timer("Apollo Collector")
@retry
async def fetch_apollo_leads(page: int = 1, per_page: int = 25):
    """
    Async Apollo collector with:
    - Quota protection
    - Proxy rotation
    - Retry logic
    - Performance logging
    """

    # 🔵 QUOTA CHECK
    if not check_quota("apollo"):
        print("⚠️ Apollo quota exceeded")
        return []

    if not API_KEY:
        print("❌ APOLLO_API_KEY missing in .env")
        return []

    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": API_KEY
    }

    payload = {
        "person_titles": [
            "Founder",
            "CEO",
            "Marketing Director",
            "Head of Growth",
            "Operations Manager"
        ],
        "page": page,
        "per_page": per_page
    }

    proxy = get_proxy()

    try:
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                BASE_URL,
                json=payload,
                headers=headers,
                proxy=proxy
            ) as response:

                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"Apollo error ({response.status}): {text}")

                data = await response.json()
                leads = []

                for p in data.get("people", []):
                    first = p.get("first_name") or ""
                    last = p.get("last_name") or ""

                    leads.append({
                        "name": f"{first} {last}".strip(),
                        "title": p.get("title") or "",
                        "email": p.get("email"),
                        "phone": p.get("phone"),
                        "company": p.get("company_name") or "",
                        "website": p.get("company_website"),
                        "country": p.get("country") or "",
                        "source": "Apollo"
                    })

                print(f"✅ Apollo page {page}: {len(leads)} leads")
                return leads

    except asyncio.TimeoutError:
        raise Exception("Apollo request timed out")

    except Exception as e:
        raise Exception(f"Apollo collector error: {e}")