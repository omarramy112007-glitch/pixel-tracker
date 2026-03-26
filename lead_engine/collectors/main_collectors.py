# lead_engine/collectors/main_collectors.py

import asyncio
from typing import List, Dict, Callable, Any

from config import MAX_CONCURRENT_TASKS

from lead_engine.collectors.apollo import fetch_apollo_leads
from lead_engine.collectors.builtwith import fetch_builtwith_leads
from lead_engine.collectors.rocketreach import fetch_rocketreach_leads
from lead_engine.collectors.thecompanies import fetch_thecompanies_leads
from lead_engine.collectors.zoho import fetch_zoho_leads
from lead_engine.collectors.serpapi import fetch_serpapi_leads
from lead_engine.collectors.explorium import enrich_company

COLLECTORS = [
    ("Apollo", fetch_apollo_leads, {}),
    ("BuiltWith", fetch_builtwith_leads, {}),
    ("TheCompanies", fetch_thecompanies_leads, {}),
    ("SERPAPI", fetch_serpapi_leads, {"query": "marketing agencies USA"}),
]

ENRICHERS = [
    ("RocketReach", fetch_rocketreach_leads),
    ("Zoho", fetch_zoho_leads),
    ("Explorium", enrich_company),
]

semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)


async def collect_all_sources() -> List[Dict[str, Any]]:
    base_tasks = [run_collector(name, func, kwargs) for name, func, kwargs in COLLECTORS]
    base_results = await asyncio.gather(*base_tasks, return_exceptions=True)

    base_leads = [lead for res in base_results if isinstance(res, list) for lead in res]
    print(f"📦 Base leads: {len(base_leads)}")

    companies = list({lead.get("company") for lead in base_leads if lead.get("company")})

    enrich_tasks = [run_collector(name, func, {"company_name": company})
                    for company in companies[:50]
                    for name, func in ENRICHERS]

    enrich_results = await asyncio.gather(*enrich_tasks, return_exceptions=True)
    enriched_leads = [lead for res in enrich_results if isinstance(res, list) for lead in res]

    all_leads = base_leads + enriched_leads
    unique_leads = deduplicate(all_leads)

    print(f"\n🔥 TOTAL UNIQUE LEADS: {len(unique_leads)}\n")
    return unique_leads


async def run_collector(name: str, collector: Callable, kwargs: dict) -> List[Dict[str, Any]]:
    async with semaphore:
        try:
            leads = await asyncio.wait_for(collector(**kwargs), timeout=30)
            if not leads:
                print(f"⚠️ {name} returned no leads")
                return []
            for lead in leads:
                lead["source"] = name
            print(f"✅ {name}: {len(leads)} leads")
            return leads
        except asyncio.TimeoutError:
            print(f"⏱️ {name} timed out")
            return []
        except Exception as e:
            print(f"❌ {name} failed: {e}")
            return []


def deduplicate(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    unique = []

    for lead in leads:
        email = (lead.get("email") or "").lower().strip()
        name = (lead.get("name") or "").lower().strip()
        company = (lead.get("company") or "").lower().strip()

        key = email if email else f"{name}|{company}"

        if key and key not in seen:
            seen.add(key)
            unique.append(lead)

    return unique