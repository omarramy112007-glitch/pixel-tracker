# outreach_engine/main.py

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from outreach_engine.database.supabase_client import supabase
from outreach_engine.processors.lead_fetcher import get_ready_leads, async_get_ready_leads
from outreach_engine.processors.lead_prioritizer import prioritize_leads
from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.processors.outreach_sender import send_bulk_emails, send_email_async
from outreach_engine.processors.follow_up_manager import (
    generate_next_email,
    update_followup,
    choose_followup_type,
    mark_lead_failed,
    mark_lead_replied,
    decide_followup_action,
)

from outreach_engine.analytics.lead_scoring import score_lead, rank_leads_by_expected_revenue
from outreach_engine.analytics.ml_revenue_model import predict_revenue_ml
from outreach_engine.analytics.pricing_optimizer import adjust_pricing
from outreach_engine.analytics.campaign_optimizer import optimize_campaign

from outreach_engine.api.dashboard_api import router as dashboard_router
from outreach_engine.api.campaign_api import router as campaign_router
from outreach_engine.database.event_repository import store_event

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

PREVIEW_COUNT = int(os.getenv("PREVIEW_COUNT", "5"))
CONCURRENCY   = int(os.getenv("CONCURRENCY", "5"))

TEST_MODE                   = os.getenv("TEST_MODE", "false").lower() == "true"
TEST_LIMIT                  = max(1, int(os.getenv("TEST_LIMIT", "1")))
TEST_LEAD_EMAIL             = os.getenv("TEST_LEAD_EMAIL", "").strip() or None
SHOW_DASHBOARD_IN_TEST_MODE = os.getenv("SHOW_DASHBOARD_IN_TEST_MODE", "true").lower() == "true"
AUTO_START_ENGINE           = os.getenv("AUTO_START_ENGINE", "false").lower() == "true"
QUIET_MODE                  = os.getenv("QUIET_MODE", "true").lower() == "true"

SENDER_NAME = os.getenv("SENDER_NAME", "").strip()
REPLY_TO    = os.getenv("REPLY_TO", "").strip() or None

print("🔥 MAIN.PY LOADED")

if QUIET_MODE:
    for logger_name in (
        "uvicorn.access", "uvicorn.error", "httpx", "httpcore",
        "googleapiclient.discovery_cache", "google.auth.transport.requests",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

ENGINE_RUN_LOCK = asyncio.Lock()
ENGINE_RUNNING  = False
ENGINE_TASK: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    ids = [
        cid for lead in leads
        if (cid := _extract_campaign_id_from_lead(lead)) is not None
    ]
    return max(set(ids), key=ids.count) if ids else None


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
            return int(res.data[0]["id"])
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
            if row.get("campaign_id") is not None:
                return int(row["campaign_id"])
    except Exception as e:
        print(f"⚠ Failed to resolve campaign_id: {e}")
    return None


def _is_initial_lead(lead: Dict[str, Any]) -> bool:
    status          = (lead.get("status") or "").lower().strip()
    last_email_sent = lead.get("last_email_sent")
    followup_step   = _safe_int(lead.get("followup_step"))
    return (
        status in {"new", "pending", "not_contacted", ""}
        and not last_email_sent
        and followup_step == 0
    )


def _show_lead_debug(lead: Dict[str, Any]) -> None:
    print(
        f"  → id:{lead.get('id')} | {lead.get('email')} | "
        f"status:{lead.get('status')} | "
        f"open:{lead.get('open_count')} | "
        f"reply:{lead.get('reply_count')} | "
        f"followup_status:{lead.get('followup_status')} | "
        f"next_followup:{lead.get('next_followup')}"
    )


def _prepare_leads(
    leads: List[Dict[str, Any]], use_optimizer: bool = True
) -> List[Dict[str, Any]]:
    if not leads:
        return []

    prioritized = prioritize_leads(leads) or list(leads)

    for lead in prioritized:
        lead["engagement_score"] = score_lead(lead)
        lead["ml_revenue"]       = predict_revenue_ml(lead)
        lead["price"]            = adjust_pricing(lead)

    if use_optimizer:
        try:
            optimized = optimize_campaign(prioritized)
            if optimized:
                prioritized = optimized
        except Exception as e:
            print(f"⚠ optimize_campaign failed: {e}")

    prioritized = rank_leads_by_expected_revenue(prioritized) or prioritized
    return prioritized


def _select_send_targets(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not leads:
        return []

    if TEST_LEAD_EMAIL:
        filtered = [
            l for l in leads
            if (l.get("email") or "").lower().strip()
            == TEST_LEAD_EMAIL.lower().strip()
        ]
        if filtered:
            print(f"🧪 TEST MODE → filtering to: {TEST_LEAD_EMAIL}")
            return filtered[:1]
        print("⚠ TEST_LEAD_EMAIL not in ready set.")
        return leads

    if TEST_MODE:
        print(f"🧪 TEST MODE → limiting to {TEST_LIMIT} lead(s)")
        return leads[:TEST_LIMIT]

    return leads


# ---------------------------------------------------------------------------
# Follow-up engine
# ---------------------------------------------------------------------------

async def _process_followup_lead(lead: Dict[str, Any]) -> str:
    """
    Delegates entirely to outreach_sender.send_email_async.

    outreach_sender is the ONLY place that:
      - builds pixel URLs with correct email_type + ts
      - sends the email via Gmail
      - updates DB state

    main.py must NOT send emails directly — doing so creates a second
    email per lead which means two pixels per open which means double
    counting in Supabase.
    """
    email       = (lead.get("email") or "").strip()
    campaign_id = _extract_campaign_id_from_lead(lead)

    if not email or campaign_id is None:
        return "skipped"

    # Check eligibility before handing off
    action = decide_followup_action(lead)

    if action is None:
        print(f"  ⏳ Not due yet or nothing to do → {email}")
        return "skipped"

    if action == "__mark_failed__":
        mark_lead_failed(email, int(campaign_id))
        return "failed"

    if action == "__mark_replied__":
        mark_lead_replied(email, int(campaign_id))
        return "replied"

    if action == "interested_followup":
        print(f"  ⚠ interested_followup blocked → {email}")
        return "skipped"

    # Hand off to outreach_sender — it handles pixel URL, Gmail, DB
    print(f"  📨 Delegating {action} → {email}")
    result = await send_email_async(
        lead_email=email,
        campaign_id=int(campaign_id),
        initial_outreach=False,
    )

    if result:
        print(f"  ✅ {action} sent → {email}")
        return "sent"
    else:
        print(f"  ❌ Send failed → {email}")
        return "error"


async def run_followup_engine_once() -> Dict[str, int]:
    print("\n" + "=" * 50)
    print("🔁 FOLLOW-UP ENGINE RUNNING")
    print("=" * 50 + "\n")

    followup_leads = await async_get_ready_leads(min_score=0, mode="followups")

    if not followup_leads:
        print("  ⚠ No follow-up leads ready.")
        return {
            "found": 0, "sent": 0, "skipped": 0,
            "error": 0, "failed": 0, "replied": 0,
        }

    print(f"  📥 FOLLOW-UP LEADS FOUND: {len(followup_leads)}\n")
    for lead in followup_leads:
        _show_lead_debug(lead)

    semaphore = asyncio.Semaphore(max(1, CONCURRENCY))

    async def _guarded(lead):
        async with semaphore:
            return await _process_followup_lead(lead)

    results = await asyncio.gather(
        *[_guarded(lead) for lead in followup_leads],
        return_exceptions=False,
    )

    counts: Dict[str, int] = {
        "found": len(followup_leads),
        "sent": 0, "skipped": 0,
        "error": 0, "failed": 0, "replied": 0,
    }
    for r in results:
        counts[r] = counts.get(r, 0) + 1

    print(f"\n  📈 Follow-up Results → {counts}")
    return counts


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def _safe_get_funnel_from_db(campaign_id: int) -> Dict[str, Any]:
    try:
        events = (
            supabase.table("lead_events")
            .select("lead_id, event_type")
            .eq("campaign_id", campaign_id)
            .execute()
            .data or []
        )
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
        total     = len(sent_ids)
        replied   = len(replied_ids)
        converted = len(converted_ids)
        return {
            "total_sent":                total,
            "replied":                   replied,
            "converted":                 converted,
            "drop_off_to_reply_pct":
                round((total - replied) / total * 100, 1) if total else 0,
            "drop_off_to_conversion_pct":
                round((replied - converted) / replied * 100, 1) if replied else 0,
        }
    except Exception as e:
        print(f"⚠ Funnel failed: {e}")
        return {
            "total_sent": 0, "replied": 0, "converted": 0,
            "drop_off_to_reply_pct": 0, "drop_off_to_conversion_pct": 0,
        }


def _print_live_dashboard(campaign_id: int) -> None:
    try:
        db_leads = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
            .data or []
        )
        events = (
            supabase.table("lead_events")
            .select("lead_id, event_type")
            .eq("campaign_id", campaign_id)
            .execute()
            .data or []
        )

        active_statuses = {"sent", "replied", "completed", "converted"}
        sent      = len([
            l for l in db_leads
            if (l.get("status") or "").lower() in active_statuses
        ])
        opens     = max(
            len([l for l in db_leads if _safe_int(l.get("open_count")) > 0]),
            len({
                e["lead_id"] for e in events
                if (e.get("event_type") or "").lower() in {"opened", "open"}
            }),
        )
        replies   = max(
            len([l for l in db_leads if _safe_int(l.get("reply_count")) > 0]),
            len({
                e["lead_id"] for e in events
                if (e.get("event_type") or "").lower() in {"reply", "replied"}
            }),
        )
        converted = max(
            len([l for l in db_leads if _safe_int(l.get("conversion_count")) > 0]),
            len({
                e["lead_id"] for e in events
                if (e.get("event_type") or "").lower() in {"converted", "conversion"}
            }),
        )
        failed_count = len([
            l for l in db_leads
            if (l.get("status") or "").lower() == "failed"
        ])
        funnel = _safe_get_funnel_from_db(campaign_id)

        print("\n📊 Dashboard (LIVE)\n------------------")
        print(f"Total Leads   : {len(db_leads)}")
        print(f"Active Sent   : {sent}")
        print(f"Failed        : {failed_count}")
        print(
            f"Open Rate     : {(opens / sent * 100):.1f}%"
            if sent else "Open Rate     : 0.0%"
        )
        print(
            f"Reply Rate    : {(replies / sent * 100):.1f}%"
            if sent else "Reply Rate    : 0.0%"
        )
        print(
            f"Conversion    : {(converted / sent * 100):.1f}%"
            if sent else "Conversion    : 0.0%"
        )
        print(
            f"\nFunnel\n"
            f"Sent: {funnel['total_sent']} | "
            f"Replied: {funnel['replied']} | "
            f"Converted: {funnel['converted']}"
        )

        if db_leads:
            print("\nTop Leads:")
            ranked = sorted(
                db_leads,
                key=lambda l: (
                    _safe_int(l.get("reply_count")),
                    _safe_int(l.get("open_count")),
                    _safe_int(l.get("conversion_count")),
                ),
                reverse=True,
            )
            for lead in ranked[:5]:
                print(
                    f"  - {lead.get('email')} | "
                    f"{lead.get('company')} | "
                    f"status:{lead.get('status')} | "
                    f"followup_status:{lead.get('followup_status')}"
                )

    except Exception as e:
        print(f"⚠ Live dashboard failed: {e}")


def display_dashboards(
    leads: Optional[List[Dict[str, Any]]] = None,
    campaign_id: Optional[int] = None,
):
    leads = leads or []
    if TEST_MODE and not SHOW_DASHBOARD_IN_TEST_MODE:
        print("\n📊 Dashboard disabled in test mode.\n")
        return

    resolved = campaign_id
    if resolved is None and leads:
        resolved = _extract_campaign_id_from_leads(leads)
    if resolved is None:
        resolved = _get_latest_campaign_id_from_db()
    if resolved:
        _print_live_dashboard(resolved)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

async def preview_sync():
    print("\n🔎 Preview (cold leads)\n")
    leads = get_ready_leads(min_score=0, mode="cold")
    if not leads:
        print("⚠ No cold leads to preview.")
        return
    leads = _prepare_leads(leads, use_optimizer=False)[:PREVIEW_COUNT]
    for lead in leads:
        email = personalize_email(lead, step=0)
        print(
            f"Lead: {lead.get('name')} | "
            f"{lead.get('company')} | "
            f"Subject: {email['subject']}"
        )
        print("---")


async def run_initial_outreach() -> List[Dict[str, Any]]:
    print("\n🚀 Starting initial cold outreach...\n")

    leads = await async_get_ready_leads(min_score=0, mode="cold")
    if not leads:
        print("⚠ No cold leads ready.")
        return []

    print(f"  📥 COLD LEADS: {len(leads)}")

    prioritized  = _prepare_leads(leads, use_optimizer=not TEST_MODE)
    initial_only = [l for l in prioritized if _is_initial_lead(l)]

    print(f"  🚨 INITIAL LEADS: {len(initial_only)}")
    for l in initial_only[:5]:
        _show_lead_debug(l)

    send_targets = _select_send_targets(initial_only)
    print(f"  📨 SEND TARGETS: {len(send_targets)}")

    if not send_targets:
        print("  ❌ No initial leads passed to sender")
        return prioritized

    # send_bulk_emails → outreach_sender.send_email_sync handles
    # pixel URL construction, Gmail send, and DB state update
    results = await send_bulk_emails(
        send_targets,
        concurrency=min(CONCURRENCY, max(1, len(send_targets))),
        initial_outreach=True,
    )

    sent   = sum(1 for r in results if r is True)
    failed = len(results) - sent
    print(f"\n  📈 Initial Outreach → sent={sent} | failed={failed}")
    return prioritized


async def _run_main_safely():
    global ENGINE_RUNNING
    if ENGINE_RUNNING:
        print("⚠ Engine already running — skipping")
        return
    async with ENGINE_RUN_LOCK:
        if ENGINE_RUNNING:
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
    if ENGINE_TASK and not ENGINE_TASK.done():
        print("⚠ Engine already running")
        return
    ENGINE_TASK = asyncio.create_task(_run_main_safely())


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n🚀 Outreach Engine startup")
    print(f"📁 Root: {ROOT_DIR}")
    print(f"🚀 AUTO_START_ENGINE: {AUTO_START_ENGINE}\n")

    if AUTO_START_ENGINE:
        print("🚀 AUTO_START_ENGINE=true → launching engine")
        _start_engine_background()

    yield

    if ENGINE_TASK and not ENGINE_TASK.done():
        ENGINE_TASK.cancel()
        await asyncio.gather(ENGINE_TASK, return_exceptions=True)
    print("🛑 Outreach Engine shutdown complete")


app = FastAPI(title="Outreach Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(campaign_router, prefix="/api")
app.include_router(dashboard_router, prefix="/analytics")


@app.get("/")
async def root():
    return {"status": "ok", "message": "Outreach Engine is live 🚀"}


@app.get("/health")
async def health():
    return {
        "status":         "ok",
        "engine_running": ENGINE_RUNNING,
    }


@app.get("/run")
@app.post("/run")
async def run_engine():
    print("🔥 /run HIT — starting full engine")
    _start_engine_background()
    return {"status": "started", "type": "cold_and_followups"}


@app.get("/status")
async def get_status():
    return {
        "engine_running":    ENGINE_RUNNING,
        "auto_start_engine": AUTO_START_ENGINE,
        "test_mode":         TEST_MODE,
    }


async def main():
    print("\n==============================")
    print(" OUTREACH ENGINE AUTO-PILOT 🚀")
    print("==============================\n")

    await preview_sync()

    cold_leads = await run_initial_outreach()

    followup_counts = await run_followup_engine_once()

    campaign_id = (
        _extract_campaign_id_from_leads(cold_leads) if cold_leads
        else _get_latest_campaign_id_from_db()
    )
    display_dashboards(cold_leads, campaign_id=campaign_id)

    print("\n==============================")
    print(" AUTO-PILOT FINISHED ✅")
    print("==============================\n")

    return {
        "cold_leads_processed": len(cold_leads),
        "followup_results":     followup_counts,
    }


if __name__ == "__main__":
    asyncio.run(main())
