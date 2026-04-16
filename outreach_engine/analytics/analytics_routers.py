# outreach_engine/analytics/analytics_routers.py

from fastapi import APIRouter, HTTPException
from outreach_engine.database.supabase_client import supabase

router = APIRouter()


# ----------------------------------------
# CRM Analytics (READ ONLY ✅)
# ----------------------------------------
@router.get("/analytics/crm/{lead_id}")
def get_lead_score(lead_id: int):
    try:
        result = (
            supabase.table("crm_analytics")
            .select("*")
            .eq("lead_id", lead_id)
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]

        # ✅ consistent empty response
        return {
            "lead_id": lead_id,
            "engagement_score": 0,
            "emails_sent": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CRM fetch failed: {str(e)}")


# ----------------------------------------
# CAMPAIGNS LIST (READ ONLY ✅)
# ----------------------------------------
@router.get("/analytics/campaigns")
def get_campaigns_list():
    try:
        result = (
            supabase.table("campaigns")
            .select("*")
            .execute()
        )

        return result.data if result.data else []

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Campaign fetch failed: {str(e)}")