# File: outreach_engine/processors/outreach_sender.py

import asyncio
from datetime import date, datetime
from typing import Optional, Any, Dict, List

from outreach_engine.core.retry import retry
from outreach_engine.core.proxy_rotator import get_next_proxy
from outreach_engine.core.email_providers import send_with_fallback
from outreach_engine.core.provider_rotator import (
    get_available_provider,
    increment_provider_usage,
)
from outreach_engine.processors.follow_up_manager import (
    determine_next_step,
    update_followup,
)
from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.core.lead_manager import get_lead

from outreach_engine.tracking.engagement_tracking import track_email_sent
from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase

from outreach_engine.analytics.lead_scoring import calculate_engagement_score
from outreach_engine.core.performance_logger import timer


def _safe_metadata(metadata: Optional[dict]) -> dict:
    """
    Recursively convert non-JSON-safe values (datetime/date/etc.) into strings.
    """
    def _convert(value: Any) -> Any:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: _convert(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_convert(v) for v in value]
        if isinstance(value, tuple):
            return tuple(_convert(v) for v in value)
        return value

    return _convert(metadata or {})


def _update_crm_analytics_safe(
    lead_id: Optional[int],
    campaign_id: int,
    increment_field: str = "emails_sent",
    increment_value: int = 1,
    engagement_score: Optional[float] = None
) -> None:
    """
    Update crm_analytics using only columns that exist in your schema.
    """
    if not lead_id:
        print("⚠ Skipping CRM update: missing lead_id")
        return

    try:
        existing = (
            supabase.table("crm_analytics")
            .select("*")
            .eq("lead_id", lead_id)
            .execute()
        )

        now = datetime.utcnow().isoformat()

        if existing.data and len(existing.data) > 0:
            row = existing.data[0]

            payload: Dict[str, Any] = {
                "lead_id": lead_id,
                "last_activity": now,
            }

            if increment_field in {"emails_sent", "opens", "clicks", "replies", "conversions"}:
                payload[increment_field] = int(row.get(increment_field, 0) or 0) + int(increment_value)

            if engagement_score is not None:
                payload["engagement_score"] = engagement_score

            supabase.table("crm_analytics").update(payload).eq("lead_id", lead_id).execute()

        else:
            payload = {
                "lead_id": lead_id,
                "emails_sent": 0,
                "opens": 0,
                "clicks": 0,
                "replies": 0,
                "conversions": 0,
                "engagement_score": engagement_score or 0,
                "last_activity": now,
            }

            if increment_field in {"emails_sent", "opens", "clicks", "replies", "conversions"}:
                payload[increment_field] = int(increment_value)

            supabase.table("crm_analytics").insert(payload).execute()

    except Exception as e:
        print(f"⚠️ crm_analytics update skipped: {e}")


@timer("send_times")
def send_email_sync(lead_email: str, campaign_id: int) -> bool:
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        print(f"⚠ Lead not found: {lead_email} | campaign={campaign_id}")
        return False

    lead_id = lead.get("id")
    if not lead_id:
        print(f"⚠ Lead missing ID: {lead_email} | campaign={campaign_id}")
        return False

    status = (lead.get("status") or "").lower()
    if status in {"replied", "opt-out", "optout", "unsubscribed", "completed"}:
        print(f"🛑 Skipping closed lead: {lead_email}")
        return False

    provider = get_available_provider()
    if not provider:
        print("🚫 Providers exhausted")
        return False

    step = determine_next_step(lead_email, campaign_id)
    if step == -1:
        print(f"🛑 Follow-up stopped for: {lead_email}")
        return False

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
            preferred_provider=provider,
        )

        increment_provider_usage(provider_used)

    except Exception as e:
        try:
            store_event(
                lead_id=lead_id,
                campaign_id=campaign_id,
                event_type="failed",
                metadata=_safe_metadata({
                    "error": str(e),
                    "step": step,
                    "provider": provider,
                }),
            )
        except Exception as store_err:
            print(f"⚠ Failed to store failure event: {store_err}")

        print(f"❌ Failed → {lead_email}: {e}")
        return False

    metadata = _safe_metadata({
        "step": step,
        "provider": provider_used,
    })

    try:
        track_email_sent(
            lead_id=lead_id,
            campaign_id=campaign_id,
            metadata=metadata,
        )
    except Exception as e:
        print(f"⚠ track_email_sent failed for {lead_email}: {e}")

    try:
        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="sent",
            metadata=metadata,
        )
    except Exception as e:
        print(f"⚠ store_event(sent) failed for {lead_email}: {e}")

    try:
        engagement_score = calculate_engagement_score(lead)
        _update_crm_analytics_safe(
            lead_id=lead_id,
            campaign_id=campaign_id,
            increment_field="emails_sent",
            increment_value=1,
            engagement_score=engagement_score,
        )
    except Exception as e:
        print(f"⚠ CRM analytics update failed for {lead_email}: {e}")

    try:
        update_followup(
            lead_email=lead_email,
            campaign_id=campaign_id,
            step=step,
            status="sent",
        )
    except Exception as e:
        print(f"⚠ follow-up update failed for {lead_email}: {e}")

    print(f"✅ Sent → {lead_email} (step {step}) via {provider_used}")
    return True


@retry
async def send_email_async(lead_email: str, campaign_id: int) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, send_email_sync, lead_email, campaign_id)


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

    enriched: List[Dict[str, Any]] = []

    for lead in leads:
        email = lead.get("email")
        campaign_id = lead.get("campaign_id") or (lead.get("raw") or {}).get("campaign_id")

        if not email or not campaign_id:
            print(f"⚠ Skipping lead missing email/campaign_id → {lead}")
            continue

        db = get_lead(email, campaign_id) or lead
        db["campaign_id"] = campaign_id

        lead_id = db.get("id")
        if not lead_id:
            print(f"⚠ Skipping lead missing ID → email={email} campaign={campaign_id}")
            continue

        score = calculate_engagement_score(db)
        db["engagement_score"] = score

        if score >= min_score:
            enriched.append(db)

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