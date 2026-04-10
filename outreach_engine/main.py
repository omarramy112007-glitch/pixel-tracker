# File: outreach_engine/main.py

import outreach_engine.utils.json_utils  # ✅ IMPORTANT (DO NOT REMOVE)

import asyncio
import os
import base64
import json
import pickle
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from googleapiclient.discovery import build

# ---------------- Core Processors ----------------
from outreach_engine.processors.lead_fetcher import get_ready_leads, async_get_ready_leads
from outreach_engine.processors.lead_prioritizer import prioritize_leads
from outreach_engine.processors.email_personalizer import personalize_email
from outreach_engine.processors.outreach_sender import send_bulk_emails
from outreach_engine.processors.follow_up_scheduler import run_scheduler_periodically

# ---------------- Analytics & Scoring ----------------
from outreach_engine.analytics.lead_scoring import score_lead, rank_leads_by_expected_revenue
from outreach_engine.analytics.dashboard_data import get_campaign_dashboard

# ---------------- Tracking ----------------
from outreach_engine.tracking.engagement_tracking import track_reply

# ---------------- Phase 18+ (ULTRA AI) ----------------
from outreach_engine.analytics.ml_revenue_model import predict_revenue_ml
from outreach_engine.analytics.pricing_optimizer import adjust_pricing
from outreach_engine.analytics.campaign_optimizer import optimize_campaign

# ---------------- Config ----------------
PREVIEW_COUNT = int(os.getenv("PREVIEW_COUNT", "5"))
CONCURRENCY = int(os.getenv("CONCURRENCY", "5"))
SCHEDULER_INTERVAL_MIN = int(os.getenv("SCHEDULER_INTERVAL_MIN", "60"))

TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
TEST_LIMIT = max(1, int(os.getenv("TEST_LIMIT", "1")))
TEST_LEAD_EMAIL = os.getenv("TEST_LEAD_EMAIL", "").strip() or None
ENABLE_FOLLOWUPS = os.getenv("ENABLE_FOLLOWUPS", "false").lower() == "true"
SHOW_DASHBOARD_IN_TEST_MODE = os.getenv("SHOW_DASHBOARD_IN_TEST_MODE", "true").lower() == "true"

# Gmail reply tracking state
HISTORY_STATE_FILE = Path(__file__).resolve().parent / "gmail_history_id.txt"

# ✅ ASGI APP
app = FastAPI(title="Outreach Engine")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/run")
async def run_engine(background_tasks: BackgroundTasks):
    """
    Starts the outreach engine in the background.
    """
    background_tasks.add_task(main)
    return {"status": "started"}


# --------------------------------------------------
# Gmail / reply tracking helpers
# --------------------------------------------------
def _find_token_file() -> Optional[Path]:
    """
    Try common token locations.
    """
    here = Path(__file__).resolve().parent
    candidates = [
        here / "token.pkl",
        here.parent / "token.pkl",
        Path.cwd() / "token.pkl",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _load_gmail_service():
    """
    Loads Gmail API client from token.pkl.
    """
    token_path = _find_token_file()
    if not token_path:
        print("⚠ token.pkl not found")
        return None

    try:
        with open(token_path, "rb") as f:
            creds = pickle.load(f)
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        print(f"⚠ Failed to load Gmail service: {e}")
        return None


def _load_last_history_id() -> Optional[str]:
    try:
        if HISTORY_STATE_FILE.exists():
            value = HISTORY_STATE_FILE.read_text(encoding="utf-8").strip()
            return value or None
    except Exception as e:
        print(f"⚠ Failed to read history state: {e}")
    return None


def _save_last_history_id(history_id: str) -> None:
    try:
        HISTORY_STATE_FILE.write_text(str(history_id).strip(), encoding="utf-8")
    except Exception as e:
        print(f"⚠ Failed to save history state: {e}")


def _get_header_value(headers: List[Dict[str, Any]], name: str) -> str:
    for h in headers or []:
        if (h.get("name") or "").lower() == name.lower():
            return h.get("value") or ""
    return ""


def _get_header_email(headers: List[Dict[str, Any]], name: str) -> str:
    raw = _get_header_value(headers, name).strip()
    if not raw:
        return ""

    match = re.search(r"<([^>]+)>", raw)
    if match:
        return match.group(1).strip().lower()

    return raw.strip().lower()


def _is_probable_reply(headers: List[Dict[str, Any]], subject: str) -> bool:
    """
    Hard filter so we ignore random inbox mail/newsletters.
    """
    subject_norm = (subject or "").strip().lower()
    in_reply_to = _get_header_value(headers, "In-Reply-To").strip()
    references = _get_header_value(headers, "References").strip()

    return bool(
        in_reply_to
        or references
        or subject_norm.startswith("re:")
    )


def _find_lead_by_thread_id(thread_id: str) -> Optional[Dict[str, Any]]:
    """
    Matches Gmail thread_id to outreach_leads.metadata.thread_id.
    """
    try:
        response = (
            get_leads_db()
            .table("outreach_leads")
            .select("*")
            .contains("metadata", {"thread_id": thread_id})
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0]
    except Exception as e:
        print(f"⚠ Lead lookup by thread_id failed: {e}")
    return None


def _find_lead_by_email(email: str) -> Optional[Dict[str, Any]]:
    """
    Fallback match for the first reply when thread_id is not stored yet.
    """
    if not email:
        return None

    try:
        response = (
            get_leads_db()
            .table("outreach_leads")
            .select("*")
            .eq("email", email)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0]
    except Exception as e:
        print(f"⚠ Lead lookup by email failed: {e}")
    return None


def get_leads_db():
    """
    Lazy import helper to avoid import cycles.
    """
    from outreach_engine.database.supabase_client import supabase
    return supabase


@app.post("/gmail/webhook")
@app.post("/tracking/gmail_push")
async def gmail_webhook(request: Request):
    """
    Receives Gmail Pub/Sub push notifications and converts real replies into tracked events.
    Ignores non-reply inbox mail.
    """
    print("📩 Gmail push received")

    body = await request.json()
    message_data = body.get("message", {}).get("data")

    if not message_data:
        print("⚠ No message data")
        return {"status": "no data"}

    try:
        decoded = base64.b64decode(message_data).decode("utf-8")
        data = json.loads(decoded)
    except Exception as e:
        print(f"⚠ Failed to decode Pub/Sub payload: {e}")
        return {"status": "bad payload"}

    history_id = data.get("historyId")
    if not history_id:
        print("⚠ No historyId")
        return {"status": "no history"}

    print(f"📨 History ID: {history_id}")

    service = _load_gmail_service()
    if not service:
        return {"status": "gmail not ready"}

    last_history_id = _load_last_history_id()

    # First run fallback: use history_id - 1 so we do not miss the first push event.
    if last_history_id:
        start_history_id = last_history_id
    else:
        try:
            start_history_id = str(max(1, int(history_id) - 1))
        except Exception:
            start_history_id = history_id

    try:
        history = service.users().history().list(
            userId="me",
            startHistoryId=start_history_id,
            historyTypes=["messageAdded"],
        ).execute()
    except Exception as e:
        print(f"⚠ Gmail history fetch failed: {e}")
        return {"status": "history failed"}

    processed = 0

    for event in history.get("history", []):
        for added in event.get("messagesAdded", []):
            try:
                message_id = added["message"]["id"]

                message = service.users().messages().get(
                    userId="me",
                    id=message_id,
                    format="full"
                ).execute()

                headers = message.get("payload", {}).get("headers", [])
                subject = _get_header_value(headers, "Subject")
                from_email = _get_header_email(headers, "From")
                thread_id = message.get("threadId")

                print("📨 New message detected")
                print(f"DEBUG → From: {from_email} | Subject: {subject} | Thread: {thread_id}")

                # Ignore anything that does not look like a reply.
                if not _is_probable_reply(headers, subject):
                    print("⚠ Ignored non-reply email")
                    continue

                # Prefer thread match. If missing, fallback to sender email for the first reply.
                lead = None
                if thread_id:
                    lead = _find_lead_by_thread_id(thread_id)

                if not lead and from_email:
                    lead = _find_lead_by_email(from_email)

                if not lead:
                    print(f"⚠ Ignored unknown reply | From: {from_email} | Thread: {thread_id}")
                    continue

                # If we found the lead via email fallback, save the thread_id so next replies match by thread.
                if thread_id:
                    try:
                        existing_metadata = lead.get("metadata") or {}
                        if not isinstance(existing_metadata, dict):
                            existing_metadata = {}
                        merged_metadata = {**existing_metadata, "thread_id": thread_id}
                        get_leads_db().table("outreach_leads").update({
                            "metadata": merged_metadata
                        }).eq("id", lead["id"]).execute()
                    except Exception as e:
                        print(f"⚠ Could not save thread_id to lead metadata: {e}")

                track_reply(
                    lead_id=lead["id"],
                    campaign_id=lead.get("campaign_id"),
                    metadata={
                        "channel": "email",
                        "source": "gmail_webhook",
                        "gmail_message_id": message_id,
                        "thread_id": thread_id,
                        "subject": subject,
                        "from_email": from_email,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )

                print(f"💬 Reply tracked for lead {lead['id']}")
                processed += 1

            except Exception as e:
                print(f"⚠ Failed to process Gmail message: {e}")

    _save_last_history_id(history_id)

    return {"status": "ok", "processed": processed}


@app.get("/preview")
async def preview_endpoint():
    """
    Preview-ready endpoint.
    """
    leads = await async_get_ready_leads(min_score=0)
    leads = _prepare_leads(leads, use_optimizer=False)[:PREVIEW_COUNT] if leads else []
    output = []

    for lead in leads:
        step = 0 if _is_initial_lead(lead) else int(lead.get("followup_step") or 0)
        email = personalize_email(lead, step=step)

        output.append({
            "name": lead.get("name"),
            "company": lead.get("company"),
            "score": lead.get("engagement_score"),
            "priority": lead.get("priority_score"),
            "ml_revenue": lead.get("ml_revenue"),
            "price": lead.get("price"),
            "step": step,
            "subject": email["subject"],
            "body": email["body"],
        })

    return JSONResponse(output)


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
    Dynamic test selection:
    - If TEST_LEAD_EMAIL matches a lead in this batch, send only to that lead.
    - If it does not match anything, turn off test restriction for this batch.
    - If TEST_LEAD_EMAIL is not set, use TEST_MODE / TEST_LIMIT normally.
    """
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
    """
    Force step 0 only for fresh leads.
    In TEST_MODE with TEST_LEAD_EMAIL, allow that exact lead to be treated as initial
    even if it already has a sent status.
    """
    if TEST_MODE and TEST_LEAD_EMAIL:
        email = (lead.get("email") or "").lower().strip()
        if email == TEST_LEAD_EMAIL.lower().strip():
            return True

    status = (lead.get("status") or "").lower().strip()
    last_email_sent = lead.get("last_email_sent")
    followup_step = int(lead.get("followup_step") or 0)

    return (
        status in {"new", "pending", "not_contacted", ""}
        and not last_email_sent
        and followup_step == 0
    )


def _get_campaign_id_from_leads(leads: List[Dict[str, Any]]) -> Optional[int]:
    """
    Pull a campaign_id from the first valid lead if available.
    """
    for lead in leads:
        campaign_id = lead.get("campaign_id")
        if campaign_id is not None:
            try:
                return int(campaign_id)
            except Exception:
                return None
    return None


def _print_local_dashboard_summary(leads: List[Dict[str, Any]]) -> None:
    """
    Fallback summary if dashboard data is missing or campaign_id isn't available.
    """
    print("\n📊 Dashboard (fallback)\n------------------")

    total = len(leads)
    sent = sum(1 for l in leads if (l.get("status") or "").lower() in {"sent", "replied", "converted"})
    opens = sum(1 for l in leads if l.get("email_opened") or (l.get("open_count") or 0) > 0)
    replies = sum(1 for l in leads if (l.get("status") or "").lower() == "replied" or (l.get("reply_count") or 0) > 0)
    converted = sum(1 for l in leads if (l.get("status") or "").lower() == "converted" or (l.get("conversion_count") or 0) > 0)

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
        step = 0 if _is_initial_lead(lead) else int(lead.get("followup_step") or 0)
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
        step = 0 if _is_initial_lead(lead) else int(lead.get("followup_step") or 0)
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

    leads = await async_get_ready_leads(min_score=0)
    if not leads:
        print("⚠ No leads ready for outreach.")
        return []

    print(f"\n📥 FETCHED LEADS: {len(leads)}")

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
def display_dashboards(leads: Optional[List[Dict[str, Any]]] = None):
    leads = leads or []

    if TEST_MODE and not SHOW_DASHBOARD_IN_TEST_MODE:
        print("\n📊 Dashboard disabled in test mode.\n")
        return

    print("\n📊 Dashboard\n------------------")

    campaign_id = _get_campaign_id_from_leads(leads)

    if not campaign_id:
        _print_local_dashboard_summary(leads)
        return

    try:
        data = get_campaign_dashboard(campaign_id)
    except Exception as e:
        print(f"⚠ Dashboard fetch failed: {e}")
        _print_local_dashboard_summary(leads)
        return

    if not data:
        _print_local_dashboard_summary(leads)
        return

    metrics = data.get("metrics") if isinstance(data, dict) else None
    metrics = metrics if isinstance(metrics, dict) else data

    print(f"Campaign ID: {data.get('campaign_id', campaign_id)}")
    print(f"Campaign Name: {data.get('campaign_name', 'Unknown Campaign')}")
    print(f"Emails Sent: {metrics.get('emails_sent', 0)}")
    print(f"SMS Sent: {metrics.get('sms_sent', 0)}")
    print(f"LinkedIn Sent: {metrics.get('linkedin_sent', 0)}")
    print(f"Calls Made: {metrics.get('calls_made', 0)}")
    print(f"Open Rate: {metrics.get('open_rate', 0)}")
    print(f"Click Rate: {metrics.get('click_rate', 0)}")
    print(f"Reply Rate: {metrics.get('reply_rate', 0)}")
    print(f"Conversion Rate: {metrics.get('conversion_rate', 0)}")

    recommendations = data.get("recommendations", []) if isinstance(data, dict) else []
    if recommendations:
        print("\nRecommendations:")
        for rec in recommendations:
            print(f"- {rec}")

    if data.get("total_expected_revenue") is not None or data.get("avg_expected_revenue") is not None:
        print("\nRevenue:")
        print(f"Total Expected Revenue: {data.get('total_expected_revenue', 0)}")
        print(f"Avg Expected Revenue: {data.get('avg_expected_revenue', 0)}")


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

    display_dashboards(leads)

    print("\n==============================")
    print(" FULL AUTO-PILOT FINISHED ✅ ")
    print("==============================\n")


if __name__ == "__main__":
    asyncio.run(main())