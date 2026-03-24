# lead/main.py — FINAL VERSION (Phase 10+ PRO MAX)

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
from processing.intent_classifier import classify_lead
from collectors.main_collectors import collect_all_sources
from core.retry import retry

BATCH_SIZE = 50


# ----------------------
# ⚡ Enrichment + Website Intelligence
# ----------------------
@retry
async def enrich_and_analyze(lead: dict) -> dict:

    # Email enrichment
    if not lead.get("email") and lead.get("website"):
        domain = lead["website"].replace("https://", "").replace("http://", "").split("/")[0]
        lead["email"] = await enrich_email(lead.get("name"), domain)

    # Website intelligence
    website_data = await analyze_website(lead.get("website"))

    lead.update({
        "automation_score": website_data.get("automation_score", 0),
        "tech_detected": website_data.get("tech_detected", []),
        "pain_signals": website_data.get("pain_signals", []),
    })

    return lead


# ----------------------
# ⚡ Batch Insert (Optimized)
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
# 🧠 Advanced Intent Upgrade
# ----------------------
def enhance_intent(lead: dict) -> dict:
    """
    Upgrade intent using multiple signals
    """

    intent = lead.get("intent_score", 0)

    # 🔥 Strong buying signals
    if lead.get("pain_signals"):
        intent += 0.2

    if lead.get("automation_score", 0) < 0.4:
        intent += 0.2  # low automation = needs help

    if lead.get("person_score", 0) > 0.7:
        intent += 0.2

    if lead.get("total_score", 0) > 1.5:
        intent += 0.2

    intent = min(intent, 1.0)
    lead["intent_score"] = intent

    # 🎯 Smart category segmentation
    if intent >= 0.85:
        lead["category"] = "hot"          # 🔥 CLOSE ASAP
    elif intent >= 0.7:
        lead["category"] = "consulting"   # ⚡ fast cash
    else:
        lead["category"] = "agency"       # 💰 long-term

    return lead


# ----------------------
# 🚀 MAIN ENGINE
# ----------------------
async def main():
    start_time = time.time()
    print("🚀 Starting ULTRA Lead Engine (Phase 10+ MAX)...\n")

    # ----------------------
    # 1️⃣ Collect
    # ----------------------
    t0 = time.time()
    all_raw_leads = await collect_all_sources()
    print(f"📦 Collected: {len(all_raw_leads)} [{time.time()-t0:.2f}s]")

    # ----------------------
    # 2️⃣ Deduplicate
    # ----------------------
    unique_leads = remove_duplicates(all_raw_leads)
    print(f"🔁 Unique: {len(unique_leads)}")

    filtered_out = 0
    skipped_no_person = 0
    to_process = []

    # ----------------------
    # 3️⃣ Clean + Extract Decision Makers
    # ----------------------
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
                entry = cleaned.copy()
                entry.update({
                    "name": person["person_name"],
                    "title": person["title"],
                    "seniority_score": person["seniority_score"],
                    "person_score": person["person_score"]
                })
                entries.append(entry)

            return entries

        except Exception as e:
            print(f"⚠️ Processing failed: {e}")
            return []

    processed = await asyncio.gather(*(process_lead(l) for l in unique_leads))

    for batch in processed:
        to_process.extend(batch)

    print(f"✅ Processed leads: {len(to_process)}")

    # ----------------------
    # 4️⃣ Enrichment
    # ----------------------
    enriched = await asyncio.gather(*(enrich_and_analyze(l) for l in to_process))
    print("✅ Enriched")

    # ----------------------
    # 5️⃣ Scoring
    # ----------------------
    try:
        title_w, industry_w = adjust_scoring_weights()
    except:
        title_w, industry_w = {}, {}

    for lead in enriched:
        try:
            lead["basic_score"] = basic_score(lead, title_weight_map=title_w, industry_weight_map=industry_w)
            lead["automation_score"] = automation_score(lead)
            lead["person_score"] = person_score(lead)
            lead["total_score"] = lead["basic_score"] + lead["automation_score"]
        except:
            continue

    print("🎯 Scored")

    # ----------------------
    # 🔥 6️⃣ Intent + Category (UPGRADED)
    # ----------------------
    classified = []
    for lead in enriched:
        try:
            lead = classify_lead(lead)
            lead = enhance_intent(lead)
            classified.append(lead)
        except:
            lead["intent_score"] = 0
            lead["category"] = "agency"
            classified.append(lead)

    print("🧠 Intent + Segmentation DONE")

    # ----------------------
    # 7️⃣ Analytics
    # ----------------------
    try:
        calculate_performance()
    except:
        pass

    # ----------------------
    # 8️⃣ Personalization
    # ----------------------
    try:
        personalized = await asyncio.gather(*(generate_personalization(l) for l in classified))
    except:
        personalized = classified

    print("🤖 Personalized")

    # ----------------------
    # 9️⃣ Insert
    # ----------------------
    await batch_insert(personalized)

    # ----------------------
    # 🔟 CRM Update
    # ----------------------
    try:
        update_crm_metrics()
    except:
        pass

    # ----------------------
    # 🏁 FINAL SUMMARY
    # ----------------------
    total = len(personalized)
    hot = len([l for l in personalized if l["category"] == "hot"])
    consulting = len([l for l in personalized if l["category"] == "consulting"])
    agency = len([l for l in personalized if l["category"] == "agency"])

    print("\n==============================")
    print("📊 FINAL SEGMENTATION")
    print(f"🔥 Hot Leads: {hot}")
    print(f"⚡ Consulting Leads: {consulting}")
    print(f"💰 Agency Leads: {agency}")
    print("==============================")

    print(f"\n🏁 Runtime: {time.time()-start_time:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())