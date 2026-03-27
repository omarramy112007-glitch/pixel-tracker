# lead_engine/main.py — FINAL FIXED VERSION

import asyncio
import time

from lead_engine.database.supabase_client import insert_lead
from lead_engine.processing.cleaner import clean_lead
from lead_engine.processing.deduplicator import remove_duplicates
from lead_engine.processing.filtering import is_target_company
from lead_engine.processing.people_extractor import extract_decision_makers

from lead_engine.processing.scorer import basic_score, automation_score, person_score
from lead_engine.processing.crm_analytics import pipeline_summary as update_crm_metrics

from lead_engine.processing.email_enrichment import enrich_email
from lead_engine.processing.website_intelligence import analyze_website

from lead_engine.database.analytics import calculate_performance
from lead_engine.database.scoring import adjust_scoring_weights

from lead_engine.processing.personalization import generate_personalization
from lead_engine.processing.intent_classifier import classify_lead

from lead_engine.collectors.main_collectors import collect_all_sources
from lead_engine.core.retry import retry

BATCH_SIZE = 50


# ----------------------
# ⚡ Enrichment
# ----------------------
@retry
async def enrich_and_analyze(lead: dict) -> dict:

    if not lead.get("email") and lead.get("website"):
        domain = lead["website"].replace("https://", "").replace("http://", "").split("/")[0]
        lead["email"] = await enrich_email(lead.get("name"), domain)

    website_data = await analyze_website(lead.get("website"))

    lead.update({
        "automation_score": website_data.get("automation_score", 0),
        "tech_detected": website_data.get("tech_detected", []),
        "pain_signals": website_data.get("pain_signals", []),
    })

    return lead


# ----------------------
# ⚡ Batch Insert
# ----------------------
@retry
async def batch_insert(leads: list):
    for i in range(0, len(leads), BATCH_SIZE):
        batch = leads[i:i+BATCH_SIZE]
        if batch:
            try:
                await insert_lead(batch)
            except Exception as e:
                print(f"⚠️ Insert failed (batch {i}): {e}")


# ----------------------
# 🧠 Intent Upgrade
# ----------------------
def enhance_intent(lead: dict) -> dict:

    intent = lead.get("intent_score", 0)

    if lead.get("pain_signals"):
        intent += 0.2

    if lead.get("automation_score", 0) < 0.4:
        intent += 0.2

    if lead.get("person_score", 0) > 0.7:
        intent += 0.2

    if lead.get("total_score", 0) > 1.5:
        intent += 0.2

    intent = min(intent, 1.0)
    lead["intent_score"] = intent

    if intent >= 0.85:
        lead["category"] = "hot"
    elif intent >= 0.7:
        lead["category"] = "consulting"
    else:
        lead["category"] = "agency"

    return lead


# ----------------------
# 🚀 MAIN ENGINE
# ----------------------
async def main():
    start_time = time.time()
    print("🚀 Starting Lead Engine...\n")

    # 1️⃣ Collect
    all_raw_leads = await collect_all_sources()
    print(f"📦 Collected: {len(all_raw_leads)}")

    # 2️⃣ Deduplicate
    unique_leads = remove_duplicates(all_raw_leads)
    print(f"🔁 Unique: {len(unique_leads)}")

    # 3️⃣ Process
    async def process_lead(lead):
        try:
            cleaned = clean_lead(lead, lead.get("source"))

            if not is_target_company(cleaned):
                return []

            people = lead.get("people", [])
            decision_makers = extract_decision_makers(cleaned, people)

            if not decision_makers:
                return []

            results = []
            for person in decision_makers:
                entry = cleaned.copy()
                entry.update({
                    "name": person["person_name"],
                    "title": person["title"],
                    "seniority_score": person["seniority_score"],
                    "person_score": person["person_score"]
                })
                results.append(entry)

            return results

        except Exception as e:
            print(f"⚠️ Processing error: {e}")
            return []

    processed = await asyncio.gather(*(process_lead(l) for l in unique_leads))

    to_process = [item for sublist in processed for item in sublist]
    print(f"✅ Processed: {len(to_process)}")

    # 4️⃣ Enrich
    enriched = await asyncio.gather(*(enrich_and_analyze(l) for l in to_process))
    print("✅ Enriched")

    # 5️⃣ Scoring
    try:
        title_w, industry_w = adjust_scoring_weights()
    except:
        title_w, industry_w = {}, {}

    for lead in enriched:
        try:
            lead["basic_score"] = basic_score(lead, title_w, industry_w)
            lead["automation_score"] = automation_score(lead)
            lead["person_score"] = person_score(lead)
            lead["total_score"] = lead["basic_score"] + lead["automation_score"]
        except:
            continue

    print("🎯 Scored")

    # 6️⃣ Intent
    classified = []
    for lead in enriched:
        try:
            lead = classify_lead(lead)
            lead = enhance_intent(lead)
        except:
            lead["intent_score"] = 0
            lead["category"] = "agency"

        classified.append(lead)

    print("🧠 Segmented")

    # 7️⃣ Personalization (SAFE)
    try:
        personalized = await asyncio.gather(*(generate_personalization(l) for l in classified))
    except Exception as e:
        print(f"⚠️ Personalization skipped: {e}")
        personalized = classified

    print("🤖 Personalized")

    # 8️⃣ Insert
    await batch_insert(personalized)

    # 9️⃣ CRM
    try:
        update_crm_metrics()
    except:
        pass

    print(f"\n🏁 Done in {time.time()-start_time:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())