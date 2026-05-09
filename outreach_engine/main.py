# outreach_engine/main.py

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from outreach_engine.database.supabase_client import supabase
from outreach_engine.processors.lead_fetcher import get_ready_leads, async_get_ready_leads
from outreach_engine.processors.lead_prioritizer import prioritize_leads
from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.processors.outreach_sender import send_bulk_emails
from outreach_engine.processors.follow_up_scheduler import run_scheduler_periodically

from outreach_engine.analytics.lead_scoring import score_lead, rank_leads_by_expected_revenue
from outreach_engine.analytics.ml_revenue_model import predict_revenue_ml
from outreach_engine.analytics.pricing_optimizer import adjust_pricing
from outreach_engine.analytics.campaign_optimizer import optimize_campaign

from outreach_engine.api.dashboard_api import router as dashboard_router
from outreach_engine.api.campaign_api import router as campaign_router

try:
    from outreach_engine.tracking.gmail_webhook import router as gmail_webhook_router
except Exception:
    gmail_webhook_router = None


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

PREVIEW_COUNT = int(os.getenv("PREVIEW_COUNT", "5"))
CONCURRENCY = max(1, int(os.getenv("CONCURRENCY", "1")))
SCHEDULER_INTERVAL_MIN = int(os.getenv("SCHEDULER_INTERVAL_MIN", "60"))

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
TEST_LIMIT = max(1, int(os.getenv("TEST_LIMIT", "1")))
TEST_LEAD_EMAIL = os.getenv("TEST_LEAD_EMAIL", "").strip() or None
ENABLE_FOLLOWUPS = os.getenv("ENABLE_FOLLOWUPS", "false").lower() == "true"
SHOW_DASHBOARD_IN_TEST_MODE = os.getenv("SHOW_DASHBOARD_IN_TEST_MODE", "true").lower() == "true"

AUTO_START_ENGINE = os.getenv("AUTO_START_ENGINE", "false").lower() == "true"
ENABLE_GMAIL_WATCHER = os.getenv("ENABLE_GMAIL_WATCHER", "false").lower() == "true"
GMAIL_WATCH_INTERVAL_SEC = int(os.getenv("GMAIL_WATCH_INTERVAL_SEC", "30"))
QUIET_MODE = os.getenv("QUIET_MODE", "true").lower() == "true"

PIXEL_BASE_URL = os.getenv("PIXEL_BASE_URL", "").strip().rstrip("/")
CLICK_TRACK_BASE_URL = os.getenv("CLICK_TRACK_BASE_URL", "").strip().rstrip("/")
VISIBLE_CTA_URL = os.getenv("VISIBLE_CTA_URL", "").strip().rstrip("/")

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(campaign_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")

if gmail_webhook_router is not None:
    app.include_router(gmail_webhook_router, prefix="/gmail")

ENGINE_RUN_LOCK = asyncio.Lock()
ENGINE_RUNNING = False
ENGINE_TASK: Optional[asyncio.Task] = None
WATCHER_TASK: Optional[asyncio.Task] = None


@app.get("/")
async def root():
    return {"status": "ok", "message": "Outreach Engine is live 🚀"}


@app.get("/health")
async def health():
    return {"status": "ok"}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _extract_campaign_id_from_lead(lead: Dict[str, Any]) -> Optional[int]:
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


def _extract_campaign_id_from_leads(leads: List[Dict[str, Any]]) -> Optional[int]:
    campaign_ids: List[int] = []
    for lead in leads:
        cid = _extract_campaign_id_from_lead(lead)
        if cid is not None:
            campaign_ids.append(cid)

    if not campaign_ids:
        return None

    return max(set(campaign_ids), key=campaign_ids.count)


def _get_latest_campaign_id_from_db() -> Optional[int]:
    try:
        res = (
            supabase.table("campaigns")
            .select("id")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            cid = res.data[0].get("id")
            if cid is not None:
                return int(cid)
    except Exception:
        pass

    try:
        res = (
            supabase.table("outreach_leads")
            .select("campaign_id")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        for row in res.data or []:
            cid = row.get("campaign_id")
            if cid is not None:
                return int(cid)
    except Exception as e:
        print(f"⚠ Failed to resolve latest campaign_id from DB: {e}")

    return None


def _is_replied_or_closed(lead: Dict[str, Any]) -> bool:
    status = (lead.get("status") or "").lower().strip()
    reply_status = lead.get("reply_status")
    reply_count = _safe_int(lead.get("reply_count"))

    if isinstance(reply_status, str):
        reply_status = reply_status.strip().lower() in {
            "1", "true", "yes", "replied", "reply", "done"
        }

    closed_statuses = {
        "replied", "converted", "won", "lost", "failed", "completed", "closed"
    }

    return (
        status in closed_statuses
        or bool(reply_status)
        or reply_count > 0
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
        and not _is_replied_or_closed(lead)
    )


def _show_lead_debug(lead: Dict[str, Any]) -> None:
    print(
        "SEND DEBUG →",
        lead.get("id"),
        lead.get("email"),
        lead.get("status"),
        lead.get("last_email_sent"),
    )


def _attach_tracking_assets(lead: Dict[str, Any]) -> Dict[str, Any]:
    lead_id = lead.get("id")
    campaign_id = _extract_campaign_id_from_lead(lead)

    visible_target = (
        VISIBLE_CTA_URL
        or lead.get("website")
        or (lead.get("raw") or {}).get("website")
        or "https://example.com"
    )

    if lead_id and PIXEL_BASE_URL:
        lead["open_tracking_url"] = (
            f"{PIXEL_BASE_URL}/open/{lead_id}"
            + (f"?campaign_id={campaign_id}" if campaign_id is not None else "")
        )

    if lead_id and CLICK_TRACK_BASE_URL:
        redirect_value = quote(visible_target, safe="")
        if campaign_id is not None:
            lead["click_tracking_url"] = (
                f"{CLICK_TRACK_BASE_URL}/click/{lead_id}"
                f"?campaign_id={campaign_id}&url={redirect_value}"
            )
        else:
            lead["click_tracking_url"] = (
                f"{CLICK_TRACK_BASE_URL}/click/{lead_id}"
                f"?url={redirect_value}"
            )

    lead["visible_cta_url"] = visible_target
    return lead


def _prepare_leads(leads: List[Dict[str, Any]], use_optimizer: bool = True) -> List[Dict[str, Any]]:
    if not leads:
        return []

    prioritized = prioritize_leads(leads) or list(leads)

    for lead in prioritized:
        lead["engagement_score"] = score_lead(lead)
        lead["ml_revenue"] = predict_revenue_ml(lead)
        lead["price"] = adjust_pricing(lead)
        _attach_tracking_assets(lead)

    if use_optimizer:
        try:
            optimized = optimize_campaign(prioritized)
            if optimized:
                prioritized = optimized
        except Exception as e:
            print(f"⚠ optimize_campaign failed — keeping prioritized leads: {e}")

    prioritized = rank_leads_by_expected_revenue(prioritized) or prioritized
    for lead in prioritized:
        _attach_tracking_assets(lead)

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


def _safe_get_funnel_from_db(campaign_id: int) -> Dict[str, Any]:
    try:
        events_res = (
            supabase.table("lead_events")
            .select("lead_id, event_type")
            .eq("campaign_id", campaign_id)
            .execute()
        )
        events = events_res.data or []

        sent_ids = {
            e["lead_id"] for e in events
            if (e.get("event_type") or "").lower() in {"sent", "email_sent"}
        }
        replied_ids = {
            e["lead_id"] for e in events
            if (e.get("event_type") or "").lower() in {"reply", "replied"}
        }
        converted_ids = {
            e["lead_id"] for e in events
            if (e.get("event_type") or "").lower() in {"converted", "conversion"}
        }

        total_sent = len(sent_ids)
        replied = len(replied_ids)
        converted = len(converted_ids)

        drop_off_reply = ((total_sent - replied) / total_sent * 100) if total_sent else 0
        drop_off_conversion = ((replied - converted) / replied * 100) if replied else 0

        return {
            "total_sent": total_sent,
            "replied": replied,
            "converted": converted,
            "drop_off_to_reply_pct": round(drop_off_reply, 1),
            "drop_off_to_conversion_pct": round(drop_off_conversion, 1),
        }
    except Exception as e:
        print(f"⚠ Funnel build failed: {e}")
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
    opens = sum(1 for l in leads if bool(l.get("email_opened")) or _safe_int(l.get("open_count")) > 0)
    replies = sum(1 for l in leads if (l.get("status") or "").lower() == "replied" or _safe_int(l.get("reply_count")) > 0)
    converted = sum(1 for l in leads if (l.get("status") or "").lower() == "converted" or _safe_int(l.get("conversion_count")) > 0)

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


def _print_live_dashboard(campaign_id: int) -> None:
    try:
        leads_res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
        )
        db_leads = leads_res.data or []

        events_res = (
            supabase.table("lead_events")
            .select("lead_id, event_type")
            .eq("campaign_id", campaign_id)
            .execute()
        )
        events = events_res.data or []

        sent = len([
            l for l in db_leads
            if (l.get("status") or "").lower() in {"sent", "replied", "converted"}
        ])

        opened_from_leads = len([
            l for l in db_leads
            if _safe_int(l.get("open_count")) > 0 or bool(l.get("email_opened"))
        ])
        replied_from_leads = len([
            l for l in db_leads
            if _safe_int(l.get("reply_count")) > 0
            or (l.get("status") or "").lower() == "replied"
            or bool(l.get("reply_status"))
        ])
        converted_from_leads = len([
            l for l in db_leads
            if _safe_int(l.get("conversion_count")) > 0 or (l.get("status") or "").lower() == "converted"
        ])

        opened_from_events = len({
            e["lead_id"] for e in events
            if (e.get("event_type") or "").lower() in {"opened", "open"}
        })
        replied_from_events = len({
            e["lead_id"] for e in events
            if (e.get("event_type") or "").lower() in {"reply", "replied"}
        })
        converted_from_events = len({
            e["lead_id"] for e in events
            if (e.get("event_type") or "").lower() in {"converted", "conversion"}
        })

        opens = max(opened_from_leads, opened_from_events)
        replies = max(replied_from_leads, replied_from_events)
        converted = max(converted_from_leads, converted_from_events)

        open_rate = (opens / sent * 100) if sent else 0
        reply_rate = (replies / sent * 100) if sent else 0
        conversion_rate = (converted / sent * 100) if sent else 0

        funnel = _safe_get_funnel_from_db(campaign_id)

        print("\n📊 Dashboard (LIVE)\n------------------")
        print(f"Leads Prepared: {len(db_leads)}")
        print(f"Emails Sent   : {sent}")
        print(f"Open Rate     : {open_rate:.1f}%")
        print(f"Reply Rate    : {reply_rate:.1f}%")
        print(f"Conversion    : {conversion_rate:.1f}%")

        print("\nFunnel")
        print(f"Sent      : {funnel.get('total_sent', 0)}")
        print(f"Replied   : {funnel.get('replied', 0)}")
        print(f"Converted : {funnel.get('converted', 0)}")

        if db_leads:
            print("\nTop Leads:")
            ranked = sorted(
                db_leads,
                key=lambda l: (
                    _safe_int(l.get("reply_count")),
                    _safe_int(l.get("open_count")),
                    _safe_int(l.get("conversion_count")),
                    _safe_int(l.get("followup_step")),
                ),
                reverse=True
            )
            for lead in ranked[:5]:
                print(f"- {lead.get('email')} | {lead.get('company')} | {lead.get('status')}")

    except Exception as e:
        print(f"⚠ Live dashboard failed: {e}")


def display_dashboards(leads: Optional[List[Dict[str, Any]]] = None, campaign_id: Optional[int] = None):
    leads = leads or []

    if TEST_MODE and not SHOW_DASHBOARD_IN_TEST_MODE:
        print("\n📊 Dashboard disabled in test mode.\n")
        return

    resolved_campaign_id = campaign_id
    if resolved_campaign_id is None and leads:
        resolved_campaign_id = _extract_campaign_id_from_leads(leads)

    if resolved_campaign_id is None:
        resolved_campaign_id = _get_latest_campaign_id_from_db()

    if not resolved_campaign_id:
        _print_local_dashboard_summary(leads)
        return

    _print_live_dashboard(resolved_campaign_id)


async def preview_sync():
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
        initial_outreach=True,
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


async def _reply_watcher_loop():
    try:
        from outreach_engine.core.reply_monitor import check_for_replies
    except Exception as e:
        print(f"⚠ Reply watcher unavailable: {e}")
        return

    while True:
        try:
            await asyncio.to_thread(check_for_replies)
        except Exception as e:
            print(f"⚠ Reply watcher error: {e}")
        await asyncio.sleep(GMAIL_WATCH_INTERVAL_SEC)


def _start_reply_watcher_background() -> None:
    global WATCHER_TASK

    if WATCHER_TASK and not WATCHER_TASK.done():
        print("⚠ Reply watcher already scheduled")
        return

    async def _run():
        try:
            from outreach_engine.tracking.gmail_watcher import main as watcher_main
            result = watcher_main()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            await _reply_watcher_loop()

    WATCHER_TASK = asyncio.create_task(_run())


@app.on_event("startup")
async def startup_event():
    print("🚀 Outreach Engine startup complete")
    print(f"📁 Root dir: {ROOT_DIR}")
    print(f"🔑 PIXEL_BASE_URL loaded: {bool(PIXEL_BASE_URL)}")
    print(f"🔗 CLICK_TRACK_BASE_URL loaded: {bool(CLICK_TRACK_BASE_URL)}")
    print(f"🧲 VISIBLE_CTA_URL loaded: {bool(VISIBLE_CTA_URL)}")
    print("📍 Registered routes:")
    for route in app.routes:
        path = getattr(route, "path", None)
        if path:
            print(f" - {path}")

    if ENABLE_GMAIL_WATCHER:
        print("📬 ENABLE_GMAIL_WATCHER=true → starting reply watcher")
        _start_reply_watcher_background()
    else:
        print("ℹ ENABLE_GMAIL_WATCHER=false → reply watcher not started")

    if AUTO_START_ENGINE:
        print("🚀 AUTO_START_ENGINE=true → launching engine")
        asyncio.create_task(_run_main_safely())
    else:
        print("ℹ AUTO_START_ENGINE=false → engine not auto-started")


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


@app.get("/run")
@app.post("/run")
async def run_engine():
    if ENGINE_RUNNING or (ENGINE_TASK and not ENGINE_TASK.done()):
        return {"status": "already_running"}

    print("🔥 RUN ENDPOINT HIT")
    _start_engine_background()
    return {"status": "started"}


async def main():
    print("\n==============================")
    print(" OUTREACH ENGINE FULL AUTO-PILOT 🚀 ")
    print("==============================\n")

    await preview_sync()

    leads = await run_initial_outreach()

    campaign_id = _extract_campaign_id_from_leads(leads) if leads else None
    if campaign_id is None:
        campaign_id = _get_latest_campaign_id_from_db()

    if leads and ENABLE_FOLLOWUPS:
        await run_followup_engine(leads)
    else:
        print("\nℹ Follow-ups skipped for test run.")

    display_dashboards(leads, campaign_id=campaign_id)

    print("\n==============================")
    print(" FULL AUTO-PILOT FINISHED ✅ ")
    print("==============================\n")


if __name__ == "__main__":
    asyncio.run(main())