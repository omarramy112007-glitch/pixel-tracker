# File: outreach_engine/main.py

import asyncio
from datetime import datetime

# ---------------- Core Processors ----------------
from outreach_engine.processors.lead_fetcher import get_ready_leads, async_get_ready_leads
from outreach_engine.processors.lead_prioritizer import prioritize_leads
from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.processors.outreach_sender import send_bulk_emails
from outreach_engine.processors.follow_up_manager import determine_next_step
from outreach_engine.processors.follow_up_scheduler import run_scheduler_periodically

# ---------------- Analytics & Scoring ----------------
from outreach_engine.analytics.lead_scoring import score_lead, rank_leads_by_expected_revenue
from outreach_engine.analytics.dashboard_data import get_campaign_dashboard, get_all_campaigns_dashboard

# ---------------- Phase 18+ (ULTRA AI) ----------------
from outreach_engine.analytics.ml_revenue_model import predict_revenue_ml
from outreach_engine.analytics.pricing_optimizer import adjust_pricing
from outreach_engine.analytics.campaign_optimizer import optimize_campaign
from outreach_engine.analytics.follow_up_rl import choose_action  # Phase 19 RL

# ---------------- Config ----------------
PREVIEW_COUNT = 5
CONCURRENCY = 5
SCHEDULER_INTERVAL_MIN = 60

# ---------------- Test Lead ----------------
TEST_LEAD = {
    "name": "Test Lead",
    "email": "your_email@example.com",  # ضع هنا ايميلك
    "company": "Test",
    "country": "Egypt",
    "tech_stack": "TestTech",
    "pain_points": "Testing",
    "automation_maturity": "Low",
    "score": 100  # عالي عشان يتخطى filter
}

# --------------------------------------------------
# Preview Mode (Sync)
# --------------------------------------------------
def preview_sync():
    print("\n🔎 Preview (sync mode)\n")
    leads = [TEST_LEAD]  # استخدم lead تجريبي

    prioritized = prioritize_leads(leads)

    for lead in prioritized:
        lead["engagement_score"] = score_lead(lead)
        lead["ml_revenue"] = predict_revenue_ml(lead)
        lead["price"] = adjust_pricing(lead)

    prioritized = optimize_campaign(prioritized)
    prioritized = rank_leads_by_expected_revenue(prioritized)

    for lead in prioritized[:PREVIEW_COUNT]:
        step = determine_next_step(lead)
        email = personalize_email(lead, step=step)
        print(f"Lead: {lead.get('name')} | Company: {lead.get('company')}")
        print(f"Score: {lead.get('engagement_score')} | Priority: {lead.get('priority_score')}")
        print(f"ML Revenue: {lead.get('ml_revenue')} | Price: {lead.get('price')}")
        print(f"Step: {step}")
        print(f"Subject: {email['subject']}")
        print(f"Body: {email['body']}")
        print("---")


# --------------------------------------------------
# Preview Mode (Async)
# --------------------------------------------------
async def preview_async():
    print("\n🔎 Preview (async mode)\n")
    leads = [TEST_LEAD]  # lead تجريبي

    prioritized = prioritize_leads(leads)

    for lead in prioritized:
        lead["engagement_score"] = score_lead(lead)
        lead["ml_revenue"] = predict_revenue_ml(lead)
        lead["price"] = adjust_pricing(lead)

    prioritized = optimize_campaign(prioritized)
    prioritized = rank_leads_by_expected_revenue(prioritized)

    for lead in prioritized[:PREVIEW_COUNT]:
        step = determine_next_step(lead)
        email = personalize_email(lead, step=step)
        print(f"Lead: {lead.get('name')} | Company: {lead.get('company')}")
        print(f"Score: {lead.get('engagement_score')} | Priority: {lead.get('priority_score')}")
        print("---")


# --------------------------------------------------
# INITIAL OUTREACH (ULTRA AI)
# --------------------------------------------------
async def run_initial_outreach():
    print("\n🚀 Starting ULTRA AI outreach...\n")
    leads = [TEST_LEAD]  # lead تجريبي

    prioritized = prioritize_leads(leads)

    for lead in prioritized:
        lead["engagement_score"] = score_lead(lead)
        lead["ml_revenue"] = predict_revenue_ml(lead)
        lead["price"] = adjust_pricing(lead)

    prioritized = optimize_campaign(prioritized)
    prioritized = rank_leads_by_expected_revenue(prioritized)

    results = await send_bulk_emails(prioritized, concurrency=CONCURRENCY)

    success = sum(1 for r in results if r is True)
    failed = len(results) - success
    print("\n📈 Outreach Summary")
    print("------------------")
    print(f"Total : {len(results)}")
    print(f"Sent  : {success}")
    print(f"Failed: {failed}")

    return prioritized


# --------------------------------------------------
# FOLLOW-UP ENGINE (RL + AI)
# --------------------------------------------------
async def run_followup_engine(leads):
    print("\n🔁 Running RL + AI Follow-ups...\n")
    await run_scheduler_periodically(leads, interval_minutes=SCHEDULER_INTERVAL_MIN, use_ai=True)


# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------
def display_dashboards(campaign_id=None):
    print("\n📊 Dashboard\n------------------")
    print("⚠ Dashboard disabled for test lead")  # disable dashboard temporarily


# --------------------------------------------------
# Phase 20 — Full Auto-Pilot Main
# --------------------------------------------------
async def main():
    print("\n==============================")
    print(" OUTREACH ENGINE FULL AUTO-PILOT 🚀 ")
    print("==============================\n")

    preview_sync()
    await preview_async()

    leads = await run_initial_outreach()

    if leads:
        leads = optimize_campaign(leads)       # Phase 18 reinforcement
        await run_followup_engine(leads)       # Phase 19 RL follow-ups

    display_dashboards()

    print("\n==============================")
    print(" FULL AUTO-PILOT FINISHED ✅ ")
    print("==============================\n")


if __name__ == "__main__":
    asyncio.run(main())