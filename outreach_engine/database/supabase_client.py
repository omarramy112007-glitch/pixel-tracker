# outreach_engine/database/supabase_client.py

import os
import asyncio
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv
from supabase import create_client, Client

# ---------------------------------------------------
# Load .env file explicitly
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
        "Create a .env file in the root outreach_engine folder with:\n"
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

# ---------------------------------------------------
# Helper Function
# ---------------------------------------------------
def get_supabase() -> Client:
    """
    Returns the global Supabase client.
    """
    return supabase


# ---------------------------------------------------
# Lead readiness helpers
# ---------------------------------------------------
READY_STATUSES = {"pending", "new", "not_contacted", "sent"}
CLOSED_STATUSES = {"replied", "failed", "converted", "unsubscribed", "opt-out", "completed"}


def _lead_quality_score(lead: Dict[str, Any]) -> float:
    """
    Derived readiness score using only columns that exist in outreach_leads.
    """
    open_count = int(lead.get("open_count", 0) or 0)
    click_count = int(lead.get("click_count", 0) or 0)
    reply_count = int(lead.get("reply_count", 0) or 0)
    conversion_count = int(lead.get("conversion_count", 0) or 0)

    return (
        open_count * 2
        + click_count * 4
        + reply_count * 10
        + conversion_count * 25
    )


# ---------------------------------------------------
# Fetch Ready Leads
# ---------------------------------------------------
def fetch_ready_leads(min_score: float = 0.0) -> List[Dict[str, Any]]:
    """
    Returns a list of leads ready for outreach.

    Uses only outreach_leads schema:
    - email
    - company
    - campaign_id
    - status
    - open_count / click_count / reply_count / conversion_count

    min_score is a derived score from existing counters.
    """
    try:
        response = (
            supabase
            .table("outreach_leads")
            .select("*")
            .execute()
        )

        all_leads = response.data or []
        ready_leads: List[Dict[str, Any]] = []

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
# Bulletproof Test Lead (Phase 1)
# ---------------------------------------------------
async def fetch_test_lead() -> List[Dict[str, Any]]:
    """
    Returns a single test lead for Phase 1 Bulletproof Test.
    Matches outreach_leads schema as closely as possible.
    """
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