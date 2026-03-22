# lead/main.py — Fully Phase 9 upgraded

import asyncio
import time
from database.supabase_client import insert_lead
from processing.cleaner import clean_lead
from processing.deduplicator import remove_duplicates
from processing.filtering import is_target_company
from processing.people_extractor import extract_decision_makers
from processing.scorer import basic_score, automation_score, person_score
from processing.crm_analytics import pipeline_summary as update_crm_metrics
from processing.email_enrichment import enrich_email
from processing.website_intelligence import analyze_website
from database.analytics import calculate_performance
from database.scoring import adjust_scoring_weights
from processing.personalization import generate_personalization
from collectors.main_collectors import collect_all_sources
from core.retry import retry

BATCH_SIZE = 50

# ----------------------
# Async Enrichment + Website Analysis
# ----------------------
@retry
async def enrich_and_analyze(lead: dict) -> dict:
    """
    Uses async enrichment and website analysis with caching & retry
    """
    # 1️⃣ Enrich email if missing
    if not lead.get("email") and lead.get("website"):
        domain = lead["website"].replace("https://", "").replace("http://", "").split("/")[0]
        lead["email"] = await enrich_email(lead.get("name"), domain)

    # 2️⃣ Analyze website
    website_data = await analyze_website(lead.get("website"))
    lead["automation_score"] = website_data.get("automation_score", 0)
    lead["tech_detected"] = website_data.get("tech_detected", [])
    lead["pain_signals"] = website_data.get("pain_signals", [])

    return lead


# ----------------------
# Batch DB Insert
# ----------------------
@retry
async def batch_insert(leads: list):
    for i in range(0, len(leads), BATCH_SIZE):
        batch = leads[i:i+BATCH_SIZE]
        if batch:
            try:
                await insert_lead(batch)
            except Exception as e:
                print(f"⚠️ Failed to insert batch starting with {batch[0].get('name')}: {e}")


# ----------------------
# Main Runner
# ----------------------
async def main():
    start_time = time.time()
    print("🚀 Starting Ultimate Async AI Lead Engine with Phase 9...\n")

    # 1️⃣ Collect
    t0 = time.time()
    all_raw_leads = await collect_all_sources()
    print(f"📦 Total raw leads collected: {len(all_raw_leads)} [{time.time()-t0:.2f}s]")

    # 2️⃣ Deduplicate
    t0 = time.time()
    unique_leads = remove_duplicates(all_raw_leads)
    print(f"🔁 Unique leads after deduplication: {len(unique_leads)} [{time.time()-t0:.2f}s]")

    filtered_out = 0
    skipped_no_person = 0
    to_insert = []

    # 3️⃣ Process + Decision Makers
    async def process_lead(lead):
        nonlocal filtered_out, skipped_no_person
        try:
            cleaned = clean_lead(lead, lead.get("source"))
            if not is_target_company(cleaned):
                filtered_out += 1
                return []
            people = lead.get("people", [])
            decision_makers = extract_decision_makers(cleaned, people)
            if not decision_makers:
                skipped_no_person += 1
                return []
            entries = []
            for person in decision_makers:
                lead_entry = cleaned.copy()
                lead_entry.update({
                    "name": person["person_name"],
                    "title": person["title"],
                    "seniority_score": person["seniority_score"],
                    "person_score": person["person_score"]
                })
                entries.append(lead_entry)
            return entries
        except Exception as e:
            print(f"⚠️ Failed processing lead {lead.get('name')}: {e}")
            return []

    t0 = time.time()
    processed_batches = await asyncio.gather(*(process_lead(lead) for lead in unique_leads))
    for batch in processed_batches:
        to_insert.extend(batch)
    print(f"✅ Phase 3 processed leads [{time.time()-t0:.2f}s]")

    # 4️⃣ Enrichment + Website Analysis
    t0 = time.time()
    enriched_leads = await asyncio.gather(*(enrich_and_analyze(lead) for lead in to_insert))
    print(f"✅ Phase 4 enriched leads [{time.time()-t0:.2f}s]")

    # 5️⃣ Phase 6 — Scoring
    t0 = time.time()
    try:
        title_weight_map, industry_weight_map = adjust_scoring_weights()
    except:
        title_weight_map, industry_weight_map = {}, {}
    for lead in enriched_leads:
        try:
            lead["basic_score"] = basic_score(lead, title_weight_map=title_weight_map, industry_weight_map=industry_weight_map)
            lead["automation_score"] = automation_score(lead)
            lead["total_score"] = lead["basic_score"] + lead["automation_score"]
            lead["person_score"] = person_score(lead)
        except Exception as e:
            print(f"⚠️ Scoring failed for {lead.get('name')}: {e}")
    print(f"🎯 Phase 6 scoring applied [{time.time()-t0:.2f}s]")

    # 6️⃣ Phase 7.3 Analytics
    try:
        performance = calculate_performance()
        print("📊 Phase 7.3 — Performance Analytics")
    except Exception as e:
        print(f"⚠️ Analytics failed: {e}")

    # 7️⃣ Phase 8 — Personalization
    t0 = time.time()
    try:
        personalized_leads = await asyncio.gather(*(generate_personalization(lead) for lead in enriched_leads))
    except:
        personalized_leads = enriched_leads
    print(f"🤖 Phase 8 personalization applied [{time.time()-t0:.2f}s]")

    # 8️⃣ Insert into DB
    t0 = time.time()
    await batch_insert(personalized_leads)
    print(f"💾 Leads inserted [{time.time()-t0:.2f}s]")

    # 9️⃣ CRM Analytics
    try:
        crm_summary = update_crm_metrics()
        print("📊 CRM Metrics Updated")
    except Exception as e:
        print(f"⚠️ CRM update failed: {e}")

    print(f"\n🏁 Total Engine Runtime: {time.time()-start_time:.2f}s")
    print(f"Filtered out: {filtered_out}")
    print(f"Skipped (no decision-maker): {skipped_no_person}")
    print(f"Inserted: {len(personalized_leads)}")


if __name__ == "__main__":
    asyncio.run(main())