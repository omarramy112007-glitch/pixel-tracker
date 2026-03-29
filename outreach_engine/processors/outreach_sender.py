# File: outreach_engine/processors/outreach_sender.py

import asyncio
from outreach_engine.core.retry import retry
from outreach_engine.core.proxy_rotator import get_next_proxy
from outreach_engine.core.email_providers import send_with_fallback
from outreach_engine.core.provider_rotator import get_available_provider, increment_provider_usage
from outreach_engine.processors.follow_up_manager import determine_next_step, generate_next_email, update_followup
from outreach_engine.core.lead_manager import get_lead

from outreach_engine.tracking.engagement_tracking import track_email_sent
from outreach_engine.database.event_repository import store_event

from outreach_engine.analytics.lead_scoring import score_lead, calculate_engagement_score
from outreach_engine.core.ab_selector import get_winning_variant
from outreach_engine.core.performance_logger import timer

# ---------------------------------------------------
# Sync Sender
# ---------------------------------------------------
@timer("send_times")
def send_email_sync(lead_email: str, campaign_id: int) -> bool:

    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return False

    if lead.get("status") == "replied":
        return False

    provider = get_available_provider()
    if not provider:
        print("🚫 Providers exhausted")
        return False

    # Determine step
    step = determine_next_step(lead_email, campaign_id)
    if step == -1:
        return False

    # A/B variant
    variant = get_winning_variant(campaign_id)

    email = generate_next_email(
        lead_email,
        campaign_id,
        sequence_name=variant if variant else "automation_outreach"
    )

    if not email["subject"]:
        return False

    try:
        proxy = get_next_proxy()
        if proxy:
            print(f"🌐 Proxy: {proxy}")

        provider_used = send_with_fallback(
            lead_email,
            email["subject"],
            email["body"],
            preferred_provider=provider
        )

        increment_provider_usage(provider_used)

        # -------------------------
        # 🔥 TRACK EVENTS
        # -------------------------
        track_email_sent(campaign_id, lead_id=lead.get("id"))

        store_event(
            lead_id=lead.get("id"),
            campaign_id=campaign_id,
            event_type="sent",
            metadata={
                "step": step,
                "provider": provider_used
            }
        )

        # -------------------------
        # Update follow-up
        # -------------------------
        update_followup(
            lead_email,
            campaign_id,
            step=step,
            status="sent"
        )

        # -------------------------
        # Update scoring
        # -------------------------
        updated = get_lead(lead_email, campaign_id)
        if updated:
            score_lead(updated)

        print(f"✅ Sent → {lead_email} (step {step}) via {provider_used}")
        return True

    except Exception as e:
        store_event(
            lead_id=lead.get("id"),
            campaign_id=campaign_id,
            event_type="failed",
            metadata={"error": str(e)}
        )

        print(f"❌ Failed → {lead_email}: {e}")
        return False


# ---------------------------------------------------
# Async Wrapper
# ---------------------------------------------------
@retry
async def send_email_async(lead_email: str, campaign_id: int) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, send_email_sync, lead_email, campaign_id)


# ---------------------------------------------------
# Bulk Sending (AI PRIORITY + Revenue-ready)
# ---------------------------------------------------
async def send_bulk_emails(
    leads: list,
    concurrency: int = 10,
    min_score: float = 0
):
    enriched = []

    for lead in leads:
        db = get_lead(lead["email"], lead["campaign_id"])
        if db:
            score = calculate_engagement_score(db)

            # 🔥 Future-ready: combine engagement + revenue later
            db["engagement_score"] = score

            if score >= min_score:
                enriched.append(db)

    # Sort by priority
    enriched.sort(key=lambda x: x["engagement_score"], reverse=True)

    semaphore = asyncio.Semaphore(concurrency)

    async def send_limited(lead):
        async with semaphore:
            return await send_email_async(
                lead["email"],
                lead["campaign_id"]
            )

    tasks = [send_limited(l) for l in enriched]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success = sum(1 for r in results if r is True)

    print(f"\n📨 Success: {success}/{len(enriched)}")

    return results