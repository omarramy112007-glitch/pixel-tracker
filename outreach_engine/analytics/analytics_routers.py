# outreach_engine/api/analytics_routes.py

from fastapi import APIRouter
from outreach_engine.database.supabase_client import supabase

router = APIRouter()


@router.get("/analytics/crm/{lead_id}")
def get_lead_score(lead_id: int):
    result = supabase.table("crm_analytics") \
        .select("*") \
        .eq("lead_id", lead_id) \
        .execute()

    if result.data:
        return result.data[0]

    return {"lead_id": lead_id, "engagement_score": 0}