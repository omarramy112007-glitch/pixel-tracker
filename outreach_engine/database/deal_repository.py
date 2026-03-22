# File: outreach_engine/database/deal_repository.py

from typing import List, Dict
from outreach_engine.database.supabase_client import supabase


# ---------------------------------------------------
# Create Deal
# ---------------------------------------------------
def create_deal(lead_id: int, campaign_id: int, value: float):
    return supabase.table("deals").insert({
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "value": value,
        "status": "open"
    }).execute()


# ---------------------------------------------------
# Update Deal Status
# ---------------------------------------------------
def update_deal_status(deal_id: int, status: str):
    return supabase.table("deals").update({
        "status": status
    }).eq("id", deal_id).execute()


# ---------------------------------------------------
# Get Deals by Campaign
# ---------------------------------------------------
def get_campaign_deals(campaign_id: int) -> List[Dict]:
    result = supabase.table("deals") \
        .select("*") \
        .eq("campaign_id", campaign_id) \
        .execute()

    return result.data