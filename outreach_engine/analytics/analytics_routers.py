# outreach_engine/analytics/analytics_routers.py

from fastapi import APIRouter
from outreach_engine.database.supabase_client import supabase

router = APIRouter()

# ----------------------------------------
# CRM Analytics
# ----------------------------------------
@router.get("/analytics/crm/{lead_id}")
def get_lead_score(lead_id: int):
    result = supabase.table("crm_analytics") \
        .select("*") \
        .eq("lead_id", lead_id) \
        .execute()

    if result.data:
        return result.data[0]

    return {"lead_id": lead_id, "engagement_score": 0}


# ----------------------------------------
# 🔥 REQUIRED FOR DASHBOARD (FIX ERROR)
# ----------------------------------------
def get_campaigns_list():
    """
    Temporary function for Bulletproof Test
    """
    result = supabase.table("campaigns") \
        .select("*") \
        .execute()

    return result.data if result.data else []