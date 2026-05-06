# outreach_engine/database/supabase_client.py

import os
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv
from supabase import create_client, Client

# ---------------------------------------------------
# Load .env
# ---------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------
# Supabase Credentials
# ---------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "❌ Supabase credentials missing.\n"
        "Create a .env file in the project root with:\n"
        "SUPABASE_URL=your_url\n"
        "SUPABASE_KEY=your_key"
    )

# ---------------------------------------------------
# Create Supabase Client
# ---------------------------------------------------
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialized")
except Exception as e:
    raise RuntimeError(f"❌ Failed to initialize Supabase client: {e}")


def get_supabase() -> Client:
    return supabase


# ---------------------------------------------------
# Lead payload helpers
# ---------------------------------------------------
def _build_payload(lead: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": lead.get("name") or lead.get("person_name"),
        "email": lead.get("email"),
        "company": lead.get("company"),
        "website": lead.get("website"),
        "industry": lead.get("industry"),
        "country": lead.get("country"),
        "title": lead.get("title"),
        "title_category": lead.get("title_category"),
        "source": lead.get("source"),
        "lead_source": lead.get("lead_source"),

        "score": lead.get("score", 0),
        "automation_score": lead.get("automation_score", 0),
        "person_score": lead.get("person_score", 0),
        "pain_score": lead.get("pain_score", 0),
        "company_score": lead.get("company_score", 0),
        "seniority_score": lead.get("seniority_score", 0),

        "tech_stack": lead.get("tech_stack"),
        "pain_points": lead.get("pain_points"),
        "automation_maturity": lead.get("automation_maturity"),

        "outreach_status": lead.get("outreach_status") or "Not Contacted",
        "reply_status": lead.get("reply_status") or "No Reply",
        "meeting_booked": bool(lead.get("meeting_booked", False)),
        "deal_status": lead.get("deal_status") or "Open",
        "deal_value": lead.get("deal_value", 0),
        "pipeline_stage": lead.get("pipeline_stage") or "Prospect",

        "open_count": int(lead.get("open_count", 0) or 0),
        "click_count": int(lead.get("click_count", 0) or 0),
        "reply_count": int(lead.get("reply_count", 0) or 0),
        "conversion_count": int(lead.get("conversion_count", 0) or 0),
        "meeting_count": int(lead.get("meeting_count", 0) or 0),
        "followup_count": int(lead.get("followup_count", 0) or 0),

        "deal_closed": bool(lead.get("deal_closed", False)),
        "email_opened": bool(lead.get("email_opened", False)),
        "link_clicked": bool(lead.get("link_clicked", False)),

        "created_at": lead.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at": lead.get("updated_at") or datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------
# Insert (Single)
# ---------------------------------------------------
def insert_lead(lead: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    email = lead.get("email")
    website = lead.get("website")

    if not email or not website:
        return None

    payload = _build_payload(lead)

    try:
        response = supabase.table("leads").upsert(
            payload,
            on_conflict=["email", "website"]
        ).execute()

        return response.data[0] if response.data else None

    except Exception as e:
        print(f"❌ insert_lead error: {e}")
        return None


# ---------------------------------------------------
# Bulk Insert
# ---------------------------------------------------
def insert_leads_bulk(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not leads:
        return []

    payloads = [
        _build_payload(l)
        for l in leads
        if l.get("email") and l.get("website")
    ]

    if not payloads:
        return []

    try:
        response = supabase.table("leads").upsert(
            payloads,
            on_conflict=["email", "website"]
        ).execute()

        print(f"✅ Bulk inserted: {len(payloads)} leads")
        return response.data or []

    except Exception as e:
        print(f"❌ Bulk insert error: {e}")
        return []


# ---------------------------------------------------
# Pipeline Updates
# ---------------------------------------------------
def update_pipeline_stage(lead_id: str, stage: str):
    supabase.table("leads").update({
        "pipeline_stage": stage,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", lead_id).execute()


def mark_contacted(lead_id: str):
    supabase.table("leads").update({
        "outreach_status": "Contacted",
        "last_contacted": datetime.now(timezone.utc).isoformat(),
        "pipeline_stage": "Contacted",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", lead_id).execute()


def mark_replied(lead_id: str):
    existing = (
        supabase.table("leads")
        .select("reply_count")
        .eq("id", lead_id)
        .limit(1)
        .execute()
    )

    current = 0
    if existing.data:
        current = int(existing.data[0].get("reply_count") or 0)

    supabase.table("leads").update({
        "reply_status": "Replied",
        "reply_count": current + 1,
        "pipeline_stage": "Qualified",
        "last_contacted": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", lead_id).execute()


def book_meeting(lead_id: str):
    supabase.table("leads").update({
        "meeting_booked": True,
        "meeting_count": 1,
        "pipeline_stage": "Proposal",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", lead_id).execute()


def close_deal(lead_id: str, value: float):
    supabase.table("leads").update({
        "deal_status": "Won",
        "deal_value": value,
        "deal_closed": True,
        "pipeline_stage": "Closed",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", lead_id).execute()


def lose_deal(lead_id: str):
    supabase.table("leads").update({
        "deal_status": "Lost",
        "pipeline_stage": "Closed",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", lead_id).execute()


# ---------------------------------------------------
# PIPE: WEEKLY RESET FILTER (CONFIGURABLE)
# ---------------------------------------------------
WEEK_WINDOW_DAYS = int(os.getenv("READY_LEADS_WINDOW_DAYS", "7"))


def _is_within_window(created_at: str) -> bool:
    if not created_at:
        return False
    try:
        created_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created_time.tzinfo is None:
            created_time = created_time.replace(tzinfo=timezone.utc)
        return created_time >= datetime.now(timezone.utc) - timedelta(days=WEEK_WINDOW_DAYS)
    except Exception:
        return False


# ---------------------------------------------------
# Outreach-ready leads
# ---------------------------------------------------
READY_STATUSES = {"pending", "new", "not_contacted", "sent"}
CLOSED_STATUSES = {"replied", "failed", "converted", "unsubscribed", "opt-out", "completed"}


def _lead_quality_score(lead: Dict[str, Any]) -> float:
    open_count = int(lead.get("open_count", 0) or 0)
    click_count = int(lead.get("click_count", 0) or 0)
    reply_count = int(lead.get("reply_count", 0) or 0)
    conversion_count = int(lead.get("conversion_count", 0) or 0)

    return (
        open_count * 2 +
        click_count * 4 +
        reply_count * 10 +
        conversion_count * 25
    )


def fetch_ready_leads(min_score: float = 0.0) -> List[Dict[str, Any]]:
    try:
        response = supabase.table("outreach_leads").select("*").execute()
        all_leads = response.data or []

        ready_leads = []

        for lead in all_leads:
            email = (lead.get("email") or "").strip()
            company = (lead.get("company") or "").strip()
            campaign_id = lead.get("campaign_id")
            status = (lead.get("status") or "pending").strip().lower()

            if not email or not company or not campaign_id:
                continue

            if status in CLOSED_STATUSES:
                continue

            if status not in READY_STATUSES:
                continue

            if WEEK_WINDOW_DAYS > 0 and not _is_within_window(lead.get("created_at")):
                continue

            score = _lead_quality_score(lead)
            lead["quality_score"] = score

            if score >= float(min_score):
                ready_leads.append(lead)

        ready_leads.sort(key=lambda x: x.get("quality_score", 0), reverse=True)
        return ready_leads

    except Exception as e:
        print(f"⚠ Failed to fetch ready leads: {e}")
        return []


# ---------------------------------------------------
# Bulletproof Test Lead
# ---------------------------------------------------
async def fetch_test_lead() -> List[Dict[str, Any]]:
    await asyncio.sleep(0)
    return [{
        "id": 999999,
        "email": "test@mycompany.com",
        "first_name": "Test",
        "last_name": "Lead",
        "company": "test",
        "industry": "test",
        "lead_source": "test",
        "campaign_id": 1,
        "followup_step": 0,
        "last_email_sent": None,
        "next_followup": None,
        "status": "pending",
        "open_count": 0,
        "click_count": 0,
        "reply_count": 0,
        "conversion_count": 0,
        "metadata": {},
        "created_at": None,
        "last_updated": None,
        "quality_score": 0,
    }]