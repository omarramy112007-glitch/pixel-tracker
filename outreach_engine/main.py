# outreach_engine/main.py

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI

from outreach_engine.processors.lead_fetcher import get_ready_leads, async_get_ready_leads
from outreach_engine.processors.lead_prioritizer import prioritize_leads
from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.processors.outreach_sender import send_bulk_emails
from outreach_engine.processors.follow_up_scheduler import run_scheduler_periodically

from outreach_engine.analytics.lead_scoring import score_lead, rank_leads_by_expected_revenue
from outreach_engine.analytics.campaign_analytics import get_real_time_metrics, get_campaign_funnel

from outreach_engine.analytics.ml_revenue_model import predict_revenue_ml
from outreach_engine.analytics.pricing_optimizer import adjust_pricing
from outreach_engine.analytics.campaign_optimizer import optimize_campaign

from outreach_engine.tracking.gmail_webhook import router as gmail_router
from outreach_engine.tracking.link_tracker import router as click_router


PREVIEW_COUNT = int(os.getenv("PREVIEW_COUNT", "5"))
CONCURRENCY = int(os.getenv("CONCURRENCY", "5"))
SCHEDULER_INTERVAL_MIN = int(os.getenv("SCHEDULER_INTERVAL_MIN", "60"))

TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
TEST_LIMIT = max(1, int(os.getenv("TEST_LIMIT", "1")))
TEST_LEAD_EMAIL = os.getenv("TEST_LEAD_EMAIL", "").strip() or None
ENABLE_FOLLOWUPS = os.getenv("ENABLE_FOLLOWUPS", "false").lower() == "true"
SHOW_DASHBOARD_IN_TEST_MODE = os.getenv("SHOW_DASHBOARD_IN_TEST_MODE", "true").lower() == "true"

AUTO_START_ENGINE = os.getenv("AUTO_START_ENGINE", "false").lower() == "true"
QUIET_MODE = os.getenv("QUIET_MODE", "true").lower() == "true"

if QUIET_MODE:
    for logger_name in (
        "uvicorn.access",
        "uvicorn.error",
        "httpx",
        "httpcore",
        "googleapiclient.discovery_cache",
        "google.auth.transport.requests",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

app = FastAPI(title="Outreach Engine")
app.include_router(gmail_router)
app.include_router(click_router)

ENGINE_RUN_LOCK = asyncio.Lock()
ENGINE_RUNNING = False
ENGINE_TASK: Optional[asyncio.Task] = None


@app.get("/")
async def root():
    return {"status": "ok", "message": "Outreach Engine is live 🚀"}


@app.get("/health")
async def health():
    return {"status": "ok"}


async def _run_main_safely():
    global ENGINE_RUNNING

    if ENGINE_RUNNING:
        print("⚠ Engine already running, skipping duplicate start")
        return

    async with ENGINE_RUN_LOCK:
        if ENGINE_RUNNING:
            print("⚠ Engine already running, skipping duplicate start")
            return

        ENGINE_RUNNING = True
        try:
            await main()
        except Exception as e:
            print(f"❌ Engine crashed: {e}")
        finally:
            ENGINE_RUNNING = False


def _start_engine_background() -> None:
    global ENGINE_TASK

    try:
        if ENGINE_TASK and not ENGINE_TASK.done():
            print("⚠ Engine task already scheduled")
            return

        ENGINE_TASK = asyncio.create_task(_run_main_safely())
    except Exception as e:
        print(f"❌ Failed to schedule engine task: {e}")


@app.on_event("startup")
async def startup_event():
    print("🚀 Outreach Engine startup complete")
    if AUTO_START_ENGINE:
        print("🚀 AUTO_START_ENGINE=true → launching engine")
        _start_engine_background()
    else:
        print("ℹ AUTO_START_ENGINE=false → engine not auto-started")


@app.get("/run")
@app.post("/run")
async def run_engine():
    print("🔥 RUN ENDPOINT HIT")
    _start_engine_background()
    return {"status": "started"}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _extract_campaign_id_from_lead(lead: Dict[str, Any]) -> Optional[int]:
    """
    Campaign id can be at top level or nested under raw.
    """
    for candidate in (
        lead.get("campaign_id"),
        (lead.get("raw") or {}).get("campaign_id"),
        (lead.get("metadata") or {}).get("campaign_id"),
    ):
        if candidate is not None:
            try:
                return int(candidate)
            except Exception:
                continue
    return None


def _prepare_leads(leads: List[Dict[str, Any]], use_optimizer: bool = True) -> List[Dict[str, Any]]:
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
        except Exception as e:
            print(f"⚠ optimize_campaign failed — keeping prioritized leads: {e}")

    prioritized = rank_leads_by_expected_revenue(prioritized) or prioritized
    return prioritized


def _select_send_targets(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not leads:
        return []

    if TEST_LEAD_EMAIL:
        filtered = [
            lead for lead in leads
            if (lead.get("email") or "").lower().strip() == TEST_LEAD_EMAIL.lower().strip()
        ]
        if filtered:
            print(f"🧪 TEST MODE ACTIVE → filtering by email: {TEST_LEAD_EMAIL}")
            return filtered[:1]

        print(f"⚠ TEST_LEAD_EMAIL={TEST_LEAD_EMAIL} not found. Sending real eligible leads for this batch.")
        return leads

    if TEST_MODE:
        print(f"🧪 TEST MODE ACTIVE → limiting to first {TEST_LIMIT} lead(s)")
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


def _is_initial_lead(lead: Dict[str, Any]) -> bool:
    if TEST_MODE and TEST_LEAD_EMAIL:
        email = (lead.get("email") or "").lower().strip()
        if email == TEST_LEAD_EMAIL.lower().strip():
            return True

    status = (lead.get("status") or "").lower().strip()
    last_email_sent = lead.get("last_email_sent")
    followup_step = _safe_int(lead.get("followup_step"))

    return (
        status in {"new", "pending", "not_contacted", ""}
        and not last_email_sent
        and followup_step == 0
    )


def _get_campaign_id_from_leads(leads: List[Dict[str, Any]]) -> Optional[int]:
    for lead in leads:
        campaign_id = _extract_campaign_id_from_lead(lead)
        if campaign_id is not None:
            return campaign_id
    return None


def _safe_get_funnel(campaign_id: int) -> Dict[str, Any]:
    try:
        return get_campaign_funnel(campaign_id) or {}
    except Exception:
        return {
            "total_sent": 0,
            "replied": 0,
            "converted": 0,
            "drop_off_to_reply_pct": 0,
            "drop_off_to_conversion_pct": 0,
        }


def _print_local_dashboard_summary(leads: List[Dict[str, Any]]) -> None:
    print("\n📊 Dashboard (fallback)\n------------------")

    total = len(leads)
    sent = sum(1 for l in leads if (l.get("status") or "").lower() in {"sent", "replied", "converted"})
    opens = sum(1 for l in leads if (l.get("email_opened") is True) or (_safe_int(l.get("open_count")) > 0))
    replies = sum(1 for l in leads if (l.get("status") or "").lower() == "replied" or (_safe_int(l.get("reply_count")) > 0))
    converted = sum(1 for l in leads if (l.get("status") or "").lower() == "converted" or (_safe_int(l.get("conversion_count")) > 0))

    open_rate = (opens / sent * 100) if sent else 0
    reply_rate = (replies / sent * 100) if sent else 0
    conversion_rate = (converted / sent * 100) if sent else 0

    print(f"Leads Prepared: {total}")
    print(f"Emails Sent   : {sent}")
    print(f"Open Rate     : {open_rate:.1f}%")
    print(f"Reply Rate    : {reply_rate:.1f}%")
    print(f"Conversion    : {conversion_rate:.1f}%")

    if total:
        print("\nTop Leads:")
        for lead in leads[:5]:
            print(f"- {lead.get('email')} | {lead.get('company')} | {lead.get('status')}")


def _print_live_dashboard(campaign_id: int, leads: List[Dict[str, Any]]) -> None:
    metrics = get_real_time_metrics(campaign_id) or {}
    funnel = _safe_get_funnel(campaign_id)

    print("\n📊 Dashboard (LIVE)\n------------------")
    print(f"Leads Prepared: {len(leads)}")
    print(f"Emails Sent   : {metrics.get('emails_sent', 0)}")
    print(f"Open Rate     : {metrics.get('open_rate', 0):.1f}%")
    print(f"Reply Rate    : {metrics.get('reply_rate', 0):.1f}%")
    print(f"Conversion    : {metrics.get('conversion_rate', 0):.1f}%")

    print("\nFunnel")
    print(f"Sent      : {funnel.get('total_sent', 0)}")
    print(f"Replied   : {funnel.get('replied', 0)}")
    print(f"Converted : {funnel.get('converted', 0)}")

    if leads:
        print("\nTop Leads:")
        for lead in leads[:5]:
            print(f"- {lead.get('email')} | {lead.get('company')} | {lead.get('status')}")


def preview_sync():
    print("\n🔎 Preview (sync mode)\n")

    leads = get_ready_leads(min_score=0)
    if not leads:
        print("⚠ No leads in preview.")
        return

    leads = _prepare_leads(leads, use_optimizer=False)
    leads = leads[:PREVIEW_COUNT]

    for lead in leads:
        step = 0 if _is_initial_lead(lead) else _safe_int(lead.get("followup_step"))
        email = personalize_email(lead, step=step)

        print(f"Lead: {lead.get('name')} | Company: {lead.get('company')}")
        print(f"Score: {lead.get('engagement_score')} | Priority: {lead.get('priority_score')}")
        print(f"ML Revenue: {lead.get('ml_revenue')} | Price: {lead.get('price')}")
        print(f"Step: {step}")
        print(f"Subject: {email['subject']}")
        print("---")


async def run_initial_outreach():
    print("\n🚀 Starting ULTRA AI outreach...\n")

    leads = await async_get_ready_leads(min_score=0)
    if not leads:
        print("⚠ No leads ready for outreach.")
        return []

    print(f"📥 FETCHED LEADS: {len(leads)}")

    prioritized = _prepare_leads(leads, use_optimizer=not TEST_MODE)

    # IMPORTANT: only initial/uncontacted leads may go into the first send batch
    initial_only = [lead for lead in prioritized if _is_initial_lead(lead)]

    print(f"🚨 BEFORE SENDING: {len(initial_only)} initial leads")
    for l in initial_only[:5]:
        _show_lead_debug(l)

    send_targets = _select_send_targets(initial_only)
    print(f"📨 SEND TARGETS: {len(send_targets)} lead(s)")

    if not send_targets:
        print("❌ No initial leads passed to sender")
        return prioritized

    results = await send_bulk_emails(
        send_targets,
        concurrency=min(CONCURRENCY, max(1, len(send_targets))),
    )

    success = sum(1 for r in results if r is True)
    failed = len(results) - success

    print("\n📈 Outreach Summary")
    print("------------------")
    print(f"Total : {len(results)}")
    print(f"Sent  : {success}")
    print(f"Failed: {failed}")

    return prioritized


async def run_followup_engine(leads):
    print("\n🔁 Running Follow-ups...\n")
    await run_scheduler_periodically(
        leads,
        interval_minutes=SCHEDULER_INTERVAL_MIN,
        use_ai=True,
    )


def display_dashboards(leads: Optional[List[Dict[str, Any]]] = None):
    leads = leads or []

    if TEST_MODE and not SHOW_DASHBOARD_IN_TEST_MODE:
        print("\n📊 Dashboard disabled in test mode.\n")
        return

    campaign_id = _get_campaign_id_from_leads(leads)

    if not campaign_id:
        _print_local_dashboard_summary(leads)
        return

    try:
        _print_live_dashboard(campaign_id, leads)
    except Exception as e:
        print(f"⚠ Live dashboard failed: {e}")
        _print_local_dashboard_summary(leads)


async def main():
    print("\n==============================")
    print(" OUTREACH ENGINE FULL AUTO-PILOT 🚀 ")
    print("==============================\n")

    preview_sync()

    leads = await run_initial_outreach()

    if leads and ENABLE_FOLLOWUPS:
        await run_followup_engine(leads)
    else:
        print("\nℹ Follow-ups skipped for test run.")

    display_dashboards(leads)

    print("\n==============================")
    print(" FULL AUTO-PILOT FINISHED ✅ ")
    print("==============================\n")


if __name__ == "__main__":
    asyncio.run(main())