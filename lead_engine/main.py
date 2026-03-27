# lead_engine/main.py — FINAL FIXED VERSION

import asyncio
import time

from lead_engine.database.supabase_client import insert_lead
from lead_engine.processing.cleaner import clean_lead
from lead_engine.processing.deduplicator import remove_duplicates
from lead_engine.processing.filtering import is_target_company
from lead_engine.processing.people_extractor import extract_decision_makers

from lead_engine.processing.scorer import basic_score, automation_score, person_score
from lead_engine.processing.crm_analytics import update_crm_metrics

from lead_engine.processing.email_enrichment import enrich_email
from lead_engine.processing.website_intelligence import analyze_website

from lead_engine.database.analytics import calculate_performance
from lead_engine.database.scoring import adjust_scoring_weights

from lead_engine.processing.personalization import generate_personalization
from lead_engine.processing.intent_classifier import classify_lead

from lead_engine.collectors.main_collectors import collect_all_sources
from lead_engine.core.retry import retry

BATCH_SIZE = 50


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


@retry
async def batch_insert(leads: list):
    for i in range(0, len(leads), BATCH_SIZE):
        batch = leads[i:i+BATCH_SIZE]
        if batch:
            try:
                await insert_lead(batch)
            except Exception as e:
                print(f"⚠️ Insert failed: {e}")


def enhance_intent(lead: dict) -> dict:

    intent = lead.get("intent_score", 0)

    if lead.get("pain_signals"):
        intent += 0.2

    if lead.get("automation_score", 0) < 0.4:
        intent += 0.2

    if lead.get("person_score", 0) > 0.7:
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


# 🔥 THIS FIXES YOUR ERROR
async def async_collect_all():
    return await collect_all_sources()


async def main():
    start_time = time.time()

    print("🚀 Starting Lead Engine...\n")

    raw = await async_collect_all()
    unique = remove_duplicates(raw)

    processed = []

    for lead in unique:
        cleaned = clean_lead(lead, lead.get("source"))

        if not is_target_company(cleaned):
            continue

        people = lead.get("people", [])
        decision_makers = extract_decision_makers(cleaned, people)

        for person in decision_makers:
            entry = cleaned.copy()
            entry.update({
                "name": person["person_name"],
                "title": person["title"],
                "person_score": person["person_score"]
            })
            processed.append(entry)

    enriched = await asyncio.gather(*(enrich_and_analyze(l) for l in processed))

    for lead in enriched:
        lead["basic_score"] = basic_score(lead)
        lead["automation_score"] = automation_score(lead)
        lead["total_score"] = lead["basic_score"] + lead["automation_score"]

        lead = classify_lead(lead)
        lead = enhance_intent(lead)

    personalized = await asyncio.gather(*(generate_personalization(l) for l in enriched))

    await batch_insert(personalized)

    try:
        update_crm_metrics()
    except:
        pass

    print(f"\n🏁 Done in {time.time()-start_time:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())