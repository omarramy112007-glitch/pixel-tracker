# outreach_engine/database/supabase_client.py

import os
from pathlib import Path
from supabase import create_client, Client
from dotenv import load_dotenv
import asyncio
from typing import List, Dict

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
# Fetch Ready Leads
# ---------------------------------------------------
def fetch_ready_leads(min_score: float = 0.0) -> List[Dict]:
    """
    Returns a list of leads ready for outreach.

    Optional filtering:
    - min_score: only return leads with engagement_score >= min_score
    """
    try:
        response = supabase.table("leads").select("*").execute()
        all_leads = response.data or []

        # Filter by min_score if exists
        ready_leads = [
            lead for lead in all_leads
            if lead.get("engagement_score", 0.0) >= min_score
        ]

        return ready_leads

    except Exception as e:
        print(f"⚠ Failed to fetch ready leads: {e}")
        return []

# ---------------------------------------------------
# Bulletproof Test Lead (Phase 1)
# ---------------------------------------------------
async def fetch_test_lead() -> List[Dict]:
    """
    Returns a single test lead for Phase 1 Bulletproof Test.
    """
    await asyncio.sleep(0)  # ensure async context
    return [{
        "email": "test@mycompany.com",
        "name": "Test Lead",
        "company": "test",
        "status": "new",
        "last_email_sent": None,
        "open_count": 0,
        "reply_count": 0
    }]