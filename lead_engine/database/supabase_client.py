# database/supabase_client.py

import os
from datetime import datetime
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from supabase import create_client, Client

# -----------------------------
# Environment Setup
# -----------------------------
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("❌ Supabase credentials missing in environment variables.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# -----------------------------
# Internal Helpers
# -----------------------------
def _build_payload(lead: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": lead.get("name"),
        "email": lead.get("email"),
        "company": lead.get("company"),
        "website": lead.get("website"),
        "industry": lead.get("industry"),
        "country": lead.get("country"),

        # Scoring
        "score": lead.get("score", 0),
        "automation_score": lead.get("automation_score", 0),

        # Intelligence
        "tech_stack": lead.get("tech_stack"),
        "pain_points": lead.get("pain_points"),
        "automation_maturity": lead.get("automation_maturity"),

        # CRM defaults
        "outreach_status": "Not Contacted",
        "reply_status": "No Reply",
        "meeting_booked": False,
        "deal_status": "Open",
        "deal_value": 0,
        "pipeline_stage": "Prospect",

        "open_count": 0,
        "reply_count": 0,
        "meeting_count": 0,
        "deal_closed": False,

        "created_at": datetime.utcnow().isoformat()
    }

# -----------------------------
# Insert (Single)
# -----------------------------
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

# -----------------------------
# 🔥 BULK INSERT (CRITICAL)
# -----------------------------
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

# -----------------------------
# Pipeline Updates
# -----------------------------
def update_pipeline_stage(lead_id: str, stage: str):
    supabase.table("leads").update({
        "pipeline_stage": stage
    }).eq("id", lead_id).execute()


def mark_contacted(lead_id: str):
    supabase.table("leads").update({
        "outreach_status": "Contacted",
        "last_contacted": datetime.utcnow().isoformat(),
        "pipeline_stage": "Contacted"
    }).eq("id", lead_id).execute()


def mark_replied(lead_id: str):
    supabase.table("leads").update({
        "reply_status": "Replied",
        "pipeline_stage": "Qualified"
    }).eq("id", lead_id).execute()


def book_meeting(lead_id: str):
    supabase.table("leads").update({
        "meeting_booked": True,
        "pipeline_stage": "Proposal"
    }).eq("id", lead_id).execute()


def close_deal(lead_id: str, value: float):
    supabase.table("leads").update({
        "deal_status": "Won",
        "deal_value": value,
        "deal_closed": True,
        "pipeline_stage": "Closed"
    }).eq("id", lead_id).execute()


def lose_deal(lead_id: str):
    supabase.table("leads").update({
        "deal_status": "Lost",
        "pipeline_stage": "Closed"
    }).eq("id", lead_id).execute()

# -----------------------------
# Fetch Leads for Outreach
# -----------------------------
def fetch_ready_leads(min_score: float = 15):
    try:
        response = (
            supabase
            .table("leads")
            .select("*")
            .not_.is_("email", None)
            .eq("outreach_status", "Not Contacted")
            .gte("score", min_score)
            .order("score", desc=True)
            .limit(500)
            .execute()
        )

        return response.data or []

    except Exception as e:
        print(f"❌ fetch_ready_leads error: {e}")
        return []