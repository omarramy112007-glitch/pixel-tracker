# File: outreach_engine/processors/outreach_sender.py

import asyncio
from typing import List, Dict, Optional

from outreach_engine.core.retry import retry
from outreach_engine.core.proxy_rotator import get_next_proxy
from outreach_engine.core.email_providers import send_with_fallback
from outreach_engine.core.provider_rotator import get_available_provider, increment_provider_usage
from outreach_engine.processors.follow_up_manager import determine_next_step, update_followup
from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.core.lead_manager import get_lead

from outreach_engine.tracking.engagement_tracking import track_email_sent
from outreach_engine.database.event_repository import store_event

from outreach_engine.analytics.lead_scoring import score_lead, calculate_engagement_score
from outreach_engine.core.performance_logger import timer


# ---------------------------------------------------
# Sync Sender
# ---------------------------------------------------
@timer("send_times")
def send_email_sync(lead_email: str, campaign_id: int) -> bool:
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        print(f"⚠ Lead not found: {lead_email} | campaign={campaign_id}")
        return False

    if (lead.get("status") or "").lower() in {"replied", "opt-out", "optout", "unsubscribed", "completed"}:
        return False

    provider = get_available_provider()
    if not provider:
        print("🚫 Providers exhausted")
        return False

    # Determine step
    step = determine_next_step(lead_email, campaign_id)
    if step == -1:
        return False

    # Personalize directly from the lead (avoids broken generate_next_email path)
    email = personalize_email(lead, step=step)

    if not email or not email.get("subject") or not email.get("body"):
        print(f"⚠ Email personalization failed for {lead_email}")
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

        lead_id = lead.get("id")
        if lead_id:
            # Correct argument order
            track_email_sent(
                lead_id=lead_id,
                campaign_id=campaign_id,
                metadata={
                    "step": step,
                    "provider": provider_used
                }
            )

            store_event(
                lead_id=lead_id,
                campaign_id=campaign_id,
                event_type="sent",
                metadata={
                    "step": step,
                    "provider": provider_used
                }
            )

        # Update follow-up state
        update_followup(
            lead_email=lead_email,
            campaign_id=campaign_id,
            step=step,
            status="sent"
        )

        # Re-score after sending
        updated = get_lead(lead_email, campaign_id)
        if updated:
            score_lead(updated)

        print(f"✅ Sent → {lead_email} (step {step}) via {provider_used}")
        return True

    except Exception as e:
        lead_id = lead.get("id")
        if lead_id:
            store_event(
                lead_id=lead_id,
                campaign_id=campaign_id,
                event_type="failed",
                metadata={
                    "error": str(e),
                    "step": step
                }
            )

        print(f"❌ Failed → {lead_email}: {e}")
        return False


# ---------------------------------------------------
# Async Wrapper
# ---------------------------------------------------
@retry
async def send_email_async(lead_email: str, campaign_id: int) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, send_email_sync, lead_email, campaign_id)


# ---------------------------------------------------
# Bulk Sending (AI PRIORITY + Revenue-ready)
# ---------------------------------------------------
async def send_bulk_emails(
    leads: list,
    concurrency: int = 10,
    min_score: float = 0,
    limit: Optional[int] = None
):
    print(f"\n🚀 send_bulk_emails CALLED with {len(leads)} input leads")

    if not leads:
        print("❌ No leads passed to sender")
        return []

    enriched = []

    for lead in leads:
        email = lead.get("email")
        campaign_id = lead.get("campaign_id") or (lead.get("raw") or {}).get("campaign_id")

        if not email or not campaign_id:
            print(f"⚠ Skipping lead missing email/campaign_id → {lead}")
            continue

        db = get_lead(email, campaign_id) or lead

        # Make sure campaign_id is always present for downstream functions
        db["campaign_id"] = campaign_id

        score = calculate_engagement_score(db)
        db["engagement_score"] = score

        if score >= min_score:
            enriched.append(db)

    # Sort by priority
    enriched.sort(key=lambda x: x.get("engagement_score", 0), reverse=True)

    if limit is not None:
        enriched = enriched[:limit]

    print(f"📨 READY TO SEND: {len(enriched)}")

    if not enriched:
        print("⚠ No eligible leads after scoring/filtering")
        return []

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
    failed = len(results) - success

    print(f"\n📨 Success: {success}/{len(enriched)}")
    print(f"❌ Failed : {failed}/{len(enriched)}")

    return results