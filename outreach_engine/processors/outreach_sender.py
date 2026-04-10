# File: outreach_engine/processors/outreach_sender.py

import asyncio
import os
import random
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
from outreach_engine.tracking.engagement_tracking import (
    track_email_sent,
    track_email_failed,
)
from outreach_engine.database.supabase_client import supabase
from outreach_engine.analytics.lead_scoring import calculate_engagement_score
from outreach_engine.core.performance_logger import timer
from outreach_engine.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------
# CONFIG
# ---------------------------------------------------
TEST_EMAIL = os.getenv("TEST_EMAIL", "").strip().lower()
MIN_SEND_DELAY_SECONDS = int(os.getenv("MIN_SEND_DELAY_SECONDS", "60"))
MAX_SEND_DELAY_SECONDS = int(os.getenv("MAX_SEND_DELAY_SECONDS", "180"))
REQUIRE_FIRST_NAME = os.getenv("REQUIRE_FIRST_NAME", "true").lower() == "true"
REQUIRE_EMAIL = os.getenv("REQUIRE_EMAIL", "true").lower() == "true"
REQUIRE_COMPANY = os.getenv("REQUIRE_COMPANY", "true").lower() == "true"

WARMUP_MODE = os.getenv("WARMUP_MODE", "false").lower() == "true"
WARMUP_MAX_SENDS = int(os.getenv("WARMUP_MAX_SENDS", "10"))


def _safe_metadata(metadata: Optional[dict]) -> dict:
    """
    Recursively convert non-JSON-safe values into strings.
    """
    def _convert(value: Any) -> Any:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: _convert(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_convert(v) for v in value]
        if isinstance(value, tuple):
            return [_convert(v) for v in value]
        return value

    return _convert(metadata or {})


def _normalize_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _is_fresh_initial_lead(lead: Dict[str, Any]) -> bool:
    """
    True only for leads that should receive the initial cold email.
    """
    status = (lead.get("status") or "").lower().strip()
    last_email_sent = lead.get("last_email_sent")
    followup_step = int(lead.get("followup_step") or 0)

    return (
        status in {"new", "pending", "not_contacted", ""}
        and not last_email_sent
        and followup_step == 0
    )


def _passes_minimum_quality(lead: Dict[str, Any]) -> bool:
    """
    Minimum quality filter:
    - first_name
    - company
    - email
    """
    email = (lead.get("email") or "").strip()
    company = (lead.get("company") or "").strip()
    first_name = (lead.get("first_name") or "").strip()

    if REQUIRE_EMAIL and not email:
        return False

    if REQUIRE_COMPANY and not company:
        return False

    if REQUIRE_FIRST_NAME and not first_name:
        return False

    return True


def _is_allowed_test_target(lead: Dict[str, Any], test_mode_active: bool) -> bool:
    """
    If test mode is active, only allow the configured test email.
    Otherwise allow all leads.
    """
    if not test_mode_active:
        return True

    if not TEST_EMAIL:
        return False

    return _normalize_email(lead.get("email")) == TEST_EMAIL


def _detect_test_mode_from_batch(leads: List[Dict[str, Any]]) -> bool:
    """
    Test mode turns on only if TEST_EMAIL exists in the current batch.
    """
    if not TEST_EMAIL:
        return False

    emails = {_normalize_email(lead.get("email")) for lead in leads}
    return TEST_EMAIL in emails


def _determine_send_step(
    lead: Dict[str, Any],
    lead_email: str,
    campaign_id: int,
    initial_outreach: bool = False,
) -> int:
    """
    Initial outreach always starts at step 0.
    Follow-ups use the smarter determine_next_step logic.
    """
    if initial_outreach or _is_fresh_initial_lead(lead):
        return 0

    return determine_next_step(lead_email, campaign_id)


def _log_system_failure(
    lead_id: Optional[int],
    campaign_id: Optional[int],
    error: Exception | str,
    *,
    step: Optional[int] = None,
    provider: Optional[str] = None,
) -> None:
    """
    Fail-safe write to system_failures.
    """
    try:
        supabase.table("system_failures").insert({
            "component": "outreach_sender",
            "error_message": str(error),
            "failure_reason": f"lead_id={lead_id}; campaign_id={campaign_id}; step={step}; provider={provider}",
            "retry_count": 0,
            "last_retry": datetime.utcnow().isoformat(),
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as db_err:
        logger.error(f"Failed to write system failure row: {db_err}")


def _update_outreach_lead_safe(
    lead_id: Optional[int],
    campaign_id: int,
    *,
    status: Optional[str] = None,
    followup_step: Optional[int] = None,
    last_email_sent: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """
    Direct fail-safe update for outreach_leads.
    Keeps the row in sync even if other tracking layers fail.
    """
    if not lead_id:
        logger.warning("Skipping outreach_leads update: missing lead_id")
        return

    try:
        existing = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .eq("campaign_id", campaign_id)
            .execute()
        )
    except Exception as e:
        logger.warning(f"Could not load existing outreach_leads row: {e}")
        existing = None

    try:
        now = datetime.utcnow().isoformat()
        payload: Dict[str, Any] = {"last_updated": now}

        if status is not None:
            payload["status"] = status

        if followup_step is not None:
            payload["followup_step"] = int(followup_step)

        if last_email_sent is not None:
            payload["last_email_sent"] = last_email_sent

        if metadata is not None:
            existing_metadata = {}
            if existing and getattr(existing, "data", None):
                existing_metadata = existing.data[0].get("metadata") or {}
                if not isinstance(existing_metadata, dict):
                    existing_metadata = {}

            merged_metadata = {**existing_metadata, **_safe_metadata(metadata)}
            payload["metadata"] = merged_metadata

        supabase.table("outreach_leads") \
            .update(payload) \
            .eq("id", lead_id) \
            .eq("campaign_id", campaign_id) \
            .execute()

    except Exception as e:
        logger.warning(f"outreach_leads update skipped: {e}")


@timer("send_times")
def send_email_sync(
    lead_email: str,
    campaign_id: int,
    initial_outreach: bool = False,
    test_mode_active: Optional[bool] = None,
) -> bool:
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        logger.warning(f"Lead not found: {lead_email} | campaign={campaign_id}")
        return False

    lead_id = lead.get("id")
    if not lead_id:
        logger.warning(f"Lead missing ID: {lead_email} | campaign={campaign_id}")
        return False

    # Make sure downstream functions have both identifiers.
    lead["id"] = lead_id
    lead["campaign_id"] = campaign_id

    if test_mode_active is None:
        test_mode_active = bool(TEST_EMAIL and _normalize_email(lead_email) == TEST_EMAIL)

    status = (lead.get("status") or "").lower().strip()
    if status in {"replied", "opt-out", "optout", "unsubscribed", "completed"}:
        logger.info(f"Skipping closed lead: {lead_email}")
        return False

    if not _passes_minimum_quality(lead):
        logger.info(f"Skipping low-quality lead: {lead_email}")
        return False

    if not _is_allowed_test_target(lead, test_mode_active):
        logger.info(f"TEST MODE active, skipping non-test lead: {lead_email}")
        return False

    provider = get_available_provider()
    if not provider:
        logger.error("Providers exhausted")
        return False

    step = _determine_send_step(
        lead=lead,
        lead_email=lead_email,
        campaign_id=campaign_id,
        initial_outreach=initial_outreach,
    )

    if step == -1:
        logger.info(f"Follow-up stopped for: {lead_email}")
        return False

    email = personalize_email(lead, step=step)
    if not email or not email.get("subject") or not email.get("body"):
        logger.error(f"Email personalization failed for {lead_email}")
        return False

    provider_used = provider

    logger.info(
        f"Sending email to {lead_email} | campaign={campaign_id} | step={step} | provider={provider}"
    )

    try:
        proxy = get_next_proxy()
        if proxy:
            logger.info(f"Proxy in use: {proxy}")

        provider_used = send_with_fallback(
            lead_email,
            email["subject"],
            email["body"],
            preferred_provider=provider,
        )
        increment_provider_usage(provider_used)

    except Exception as e:
        failure_metadata = _safe_metadata({
            "error": str(e),
            "step": step,
            "provider": provider,
            "initial_outreach": initial_outreach,
        })

        _update_outreach_lead_safe(
            lead_id,
            campaign_id,
            status="failed",
            followup_step=step,
            metadata=failure_metadata,
        )

        try:
            track_email_failed(
                lead_id=lead_id,
                campaign_id=campaign_id,
                metadata=failure_metadata,
            )
        except Exception as track_err:
            logger.warning(f"track_email_failed failed for {lead_email}: {track_err}")

        _log_system_failure(
            lead_id,
            campaign_id,
            e,
            step=step,
            provider=provider,
        )

        try:
            update_followup(
                lead_email=lead_email,
                campaign_id=campaign_id,
                step=step,
                status="failed",
            )
        except Exception as e3:
            logger.warning(f"follow-up failure update failed for {lead_email}: {e3}")

        logger.error(f"Failed sending to {lead_email}: {e}")
        return False

    success_metadata = _safe_metadata({
        "step": step,
        "provider": provider_used,
        "initial_outreach": initial_outreach,
    })

    now = datetime.utcnow().isoformat()

    _update_outreach_lead_safe(
        lead_id,
        campaign_id,
        status="sent",
        followup_step=step,
        last_email_sent=now,
        metadata=success_metadata,
    )

    try:
        track_email_sent(
            lead_id=lead_id,
            campaign_id=campaign_id,
            metadata=success_metadata,
        )
    except Exception as e:
        logger.warning(f"track_email_sent failed for {lead_email}: {e}")

    try:
        update_followup(
            lead_email=lead_email,
            campaign_id=campaign_id,
            step=step,
            status="sent",
        )
    except Exception as e:
        logger.warning(f"follow-up update failed for {lead_email}: {e}")

    logger.info(f"Email sent successfully → {lead_email} (step {step}) via {provider_used}")
    return True


@retry
async def send_email_async(
    lead_email: str,
    campaign_id: int,
    initial_outreach: bool = False,
    test_mode_active: Optional[bool] = None,
) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        send_email_sync,
        lead_email,
        campaign_id,
        initial_outreach,
        test_mode_active,
    )


async def send_bulk_emails(
    leads: list,
    concurrency: int = 10,
    min_score: float = 0,
    limit: Optional[int] = None,
    initial_outreach: bool = False,
):
    logger.info(f"send_bulk_emails called with {len(leads)} input leads")

    if not leads:
        logger.warning("No leads passed to sender")
        return []

    test_mode_active = _detect_test_mode_from_batch(leads)
    if test_mode_active:
        logger.info(f"TEST MODE AUTO-ACTIVE → {TEST_EMAIL}")
    else:
        logger.info("TEST MODE OFF → normal send flow")

    enriched: List[Dict[str, Any]] = []

    for lead in leads:
        email = lead.get("email")
        campaign_id = lead.get("campaign_id") or (lead.get("raw") or {}).get("campaign_id")

        if not email or not campaign_id:
            logger.warning(f"Skipping lead missing email/campaign_id → {lead}")
            continue

        db = get_lead(email, campaign_id) or lead
        db["campaign_id"] = campaign_id

        lead_id = db.get("id")
        if not lead_id:
            logger.warning(f"Skipping lead missing ID → email={email} campaign={campaign_id}")
            continue

        # Make sure the downstream personalizer/tracker can resolve the lead.
        db["id"] = lead_id

        if initial_outreach and not _is_fresh_initial_lead(db):
            logger.info(f"Skipping non-fresh lead in initial outreach → {email}")
            continue

        if not _passes_minimum_quality(db):
            logger.info(f"Skipping low-quality lead → {email}")
            continue

        if not _is_allowed_test_target(db, test_mode_active):
            logger.info(f"TEST MODE active, skipping non-test lead → {email}")
            continue

        score = calculate_engagement_score(db)
        db["engagement_score"] = score

        if score >= min_score:
            enriched.append(db)

    enriched.sort(key=lambda x: x.get("engagement_score", 0), reverse=True)

    if WARMUP_MODE:
        warmup_cap = max(1, WARMUP_MAX_SENDS)
        if limit is None:
            limit = warmup_cap
        else:
            limit = min(limit, warmup_cap)
        logger.info(f"WARMUP_MODE active → limiting send batch to {limit} lead(s)")

    if limit is not None:
        enriched = enriched[:limit]

    logger.info(f"READY TO SEND: {len(enriched)}")

    if not enriched:
        logger.warning("No eligible leads after scoring/filtering")
        return []

    semaphore = asyncio.Semaphore(max(1, concurrency))
    send_lock = asyncio.Lock()

    min_delay = max(0, int(MIN_SEND_DELAY_SECONDS))
    max_delay = max(min_delay, int(MAX_SEND_DELAY_SECONDS))

    async def send_limited(lead):
        async with semaphore:
            async with send_lock:
                delay_seconds = random.randint(min_delay, max_delay)
                logger.info(f"Delay {delay_seconds}s before sending → {lead.get('email')}")
                await asyncio.sleep(delay_seconds)

                return await send_email_async(
                    lead["email"],
                    lead["campaign_id"],
                    initial_outreach=initial_outreach,
                    test_mode_active=test_mode_active,
                )

    tasks = [send_limited(l) for l in enriched]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success = sum(1 for r in results if r is True)
    failed = len(results) - success

    logger.info(f"Success: {success}/{len(enriched)}")
    logger.info(f"Failed: {failed}/{len(enriched)}")

    return results