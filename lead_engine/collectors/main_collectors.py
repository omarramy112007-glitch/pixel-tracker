# collectors/main_collectors.py

import asyncio
from typing import List, Dict, Callable, Any

from config import MAX_CONCURRENT_TASKS

from collectors.apollo import fetch_apollo_leads
from collectors.builtwith import fetch_builtwith_leads
from collectors.rocketreach import fetch_rocketreach_leads
from collectors.thecompanies import fetch_thecompanies_leads
from collectors.zoho import fetch_zoho_leads
from collectors.serpapi import fetch_serpapi_leads
from collectors.explorium import enrich_company

# -----------------------------
# Collectors Config
# -----------------------------
COLLECTORS = [
    ("Apollo", fetch_apollo_leads, {}),
    ("BuiltWith", fetch_builtwith_leads, {}),
    ("TheCompanies", fetch_thecompanies_leads, {}),
    ("SERPAPI", fetch_serpapi_leads, {"query": "marketing agencies USA"}),
]

# Enrichment collectors (run AFTER we have companies)
ENRICHERS = [
    ("RocketReach", fetch_rocketreach_leads),
    ("Zoho", fetch_zoho_leads),
    ("Explorium", enrich_company),
]

# -----------------------------
# Concurrency Control
# -----------------------------
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)


# -----------------------------
# Main Collector
# -----------------------------
async def collect_all_sources() -> List[Dict[str, Any]]:
    """Collect leads + enrich them (2-stage pipeline)"""

    # ---------- Stage 1: Base collectors ----------
    base_tasks = [
        run_collector(name, func, kwargs)
        for name, func, kwargs in COLLECTORS
    ]

    base_results = await asyncio.gather(*base_tasks, return_exceptions=True)

    base_leads = []
    for res in base_results:
        if isinstance(res, list):
            base_leads.extend(res)

    print(f"📦 Base leads: {len(base_leads)}")

    # ---------- Extract unique companies ----------
    companies = list({
        lead.get("company")
        for lead in base_leads
        if lead.get("company")
    })

    # ---------- Stage 2: Enrichment ----------
    enrich_tasks = []
    for company in companies[:50]:  # 🔥 limit to avoid explosion
        for name, func in ENRICHERS:
            enrich_tasks.append(
                run_collector(name, func, {"company_name": company})
            )

    enrich_results = await asyncio.gather(*enrich_tasks, return_exceptions=True)

    enriched_leads = []
    for res in enrich_results:
        if isinstance(res, list):
            enriched_leads.extend(res)

    # ---------- Merge ----------
    all_leads = base_leads + enriched_leads

    # ---------- Dedup ----------
    unique_leads = deduplicate(all_leads)

    print(f"\n🔥 TOTAL UNIQUE LEADS: {len(unique_leads)}\n")
    return unique_leads


# -----------------------------
# Collector Runner (SAFE)
# -----------------------------
async def run_collector(name: str, collector: Callable, kwargs: dict) -> List[Dict[str, Any]]:
    async with semaphore:
        try:
            # Timeout protection
            leads = await asyncio.wait_for(
                collector(**kwargs),
                timeout=30
            )

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


# -----------------------------
# Smart Deduplication
# -----------------------------
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