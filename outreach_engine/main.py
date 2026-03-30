# File: outreach_engine/main.py

import asyncio
import os
from typing import Any, Dict, List, Optional

# ---------------- Core Processors ----------------
from outreach_engine.processors.lead_fetcher import get_ready_leads, async_get_ready_leads
from outreach_engine.processors.lead_prioritizer import prioritize_leads
from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.processors.outreach_sender import send_bulk_emails
from outreach_engine.processors.follow_up_scheduler import run_scheduler_periodically

# ---------------- Analytics & Scoring ----------------
from outreach_engine.analytics.lead_scoring import score_lead, rank_leads_by_expected_revenue

# ---------------- Phase 18+ (ULTRA AI) ----------------
from outreach_engine.analytics.ml_revenue_model import predict_revenue_ml
from outreach_engine.analytics.pricing_optimizer import adjust_pricing
from outreach_engine.analytics.campaign_optimizer import optimize_campaign

# ---------------- Config ----------------
PREVIEW_COUNT = int(os.getenv("PREVIEW_COUNT", "5"))
CONCURRENCY = int(os.getenv("CONCURRENCY", "5"))
SCHEDULER_INTERVAL_MIN = int(os.getenv("SCHEDULER_INTERVAL_MIN", "60"))

# Test mode: send to 1 lead only unless disabled
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
TEST_LIMIT = int(os.getenv("TEST_LIMIT", "1"))

# Optional: force a specific test lead email
TEST_LEAD_EMAIL = os.getenv("TEST_LEAD_EMAIL", "").strip() or None

# Follow-up engine disabled by default during test
ENABLE_FOLLOWUPS = os.getenv("ENABLE_FOLLOWUPS", "false").lower() == "true"


# --------------------------------------------------
# Helpers
# --------------------------------------------------
def _prepare_leads(leads: List[Dict[str, Any]], use_optimizer: bool = True) -> List[Dict[str, Any]]:
    """
    Score + rank leads safely.
    If optimizer removes everything, keep the original prioritized list.
    """
    if not leads:
        return []

    prioritized = prioritize_leads(leads) or list(leads)

    for lead in prioritized:
        lead["engagement_score"] = score_lead(lead)
        lead["ml_revenue"] = predict_revenue_ml(lead)
        lead["price"] = adjust_pricing(lead)

    if use_optimizer:
        try:
            optimized = optimize_campaign(prioritized)
            if optimized:
                prioritized = optimized
            else:
                print("⚠ optimize_campaign returned no leads — keeping prioritized leads.")
        except Exception as e:
            print(f"⚠ optimize_campaign failed — keeping prioritized leads: {e}")

    prioritized = rank_leads_by_expected_revenue(prioritized) or prioritized
    return prioritized


def _select_send_targets(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    In test mode: send to one lead only.
    If TEST_LEAD_EMAIL is set, use that exact lead.
    """
    if not leads:
        return []

    if TEST_LEAD_EMAIL:
        filtered = [
            lead for lead in leads
            if (lead.get("email") or "").lower().strip() == TEST_LEAD_EMAIL.lower().strip()
        ]
        if filtered:
            return filtered[:1]
        print(f"⚠ TEST_LEAD_EMAIL={TEST_LEAD_EMAIL} not found. Falling back to first lead.")
        return leads[:1]

    if TEST_MODE:
        return leads[:TEST_LIMIT]

    return leads


def _show_lead_debug(lead: Dict[str, Any]) -> None:
    print(
        "SEND DEBUG →",
        lead.get("id"),
        lead.get("email"),
        lead.get("status"),
        lead.get("last_email_sent"),
    )


# --------------------------------------------------
# Preview Mode (Sync)
# --------------------------------------------------
def preview_sync():
    print("\n🔎 Preview (sync mode)\n")

    leads = get_ready_leads(min_score=0)
    if not leads:
        print("⚠ No leads in preview.")
        return

    leads = _prepare_leads(leads, use_optimizer=False)
    leads = leads[:PREVIEW_COUNT]

    for lead in leads:
        step = int(lead.get("followup_step") or 0)
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

    leads = await async_get_ready_leads(min_score=0)
    if not leads:
        print("⚠ No async leads.")
        return

    leads = _prepare_leads(leads, use_optimizer=False)
    leads = leads[:PREVIEW_COUNT]

    for lead in leads:
        step = int(lead.get("followup_step") or 0)
        email = personalize_email(lead, step=step)

        print(f"Lead: {lead.get('name')} | Company: {lead.get('company')}")
        print(f"Score: {lead.get('engagement_score')} | Priority: {lead.get('priority_score')}")
        print(f"Step: {step}")
        print(f"Subject: {email['subject']}")
        print("---")


# --------------------------------------------------
# INITIAL OUTREACH
# --------------------------------------------------
async def run_initial_outreach():
    print("\n🚀 Starting ULTRA AI outreach...\n")

    # For testing, do not use hard filters
    leads = await async_get_ready_leads(min_score=0)
    if not leads:
        print("⚠ No leads ready for outreach.")
        return []

    print(f"\n📥 FETCHED LEADS: {len(leads)}")

    # Keep optimizer OFF in test mode so leads do not get dropped
    prioritized = _prepare_leads(leads, use_optimizer=not TEST_MODE)

    print(f"\n🚨 BEFORE SENDING: {len(prioritized)} leads")

    for l in prioritized[:5]:
        _show_lead_debug(l)

    send_targets = _select_send_targets(prioritized)
    print(f"\n📨 SEND TARGETS: {len(send_targets)} lead(s)")

    if not send_targets:
        print("❌ No leads passed to sender")
        return prioritized

    results = await send_bulk_emails(
        send_targets,
        concurrency=min(CONCURRENCY, max(1, len(send_targets)))
    )

    success = sum(1 for r in results if r is True)
    failed = len(results) - success

    print("\n📈 Outreach Summary")
    print("------------------")
    print(f"Total : {len(results)}")
    print(f"Sent  : {success}")
    print(f"Failed: {failed}")

    return prioritized


# --------------------------------------------------
# FOLLOW-UP ENGINE
# --------------------------------------------------
async def run_followup_engine(leads):
    print("\n🔁 Running Follow-ups...\n")
    await run_scheduler_periodically(
        leads,
        interval_minutes=SCHEDULER_INTERVAL_MIN,
        use_ai=True
    )


# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------
def display_dashboards():
    print("\n📊 Dashboard\n------------------")
    print("⚠ Dashboard disabled for test lead")


# --------------------------------------------------
# MAIN
# --------------------------------------------------
async def main():
    print("\n==============================")
    print(" OUTREACH ENGINE FULL AUTO-PILOT 🚀 ")
    print("==============================\n")

    preview_sync()
    await preview_async()

    leads = await run_initial_outreach()

    if leads and ENABLE_FOLLOWUPS:
        await run_followup_engine(leads)
    else:
        print("\nℹ Follow-ups skipped for test run.")

    display_dashboards()

    print("\n==============================")
    print(" FULL AUTO-PILOT FINISHED ✅ ")
    print("==============================\n")


if __name__ == "__main__":
    asyncio.run(main())