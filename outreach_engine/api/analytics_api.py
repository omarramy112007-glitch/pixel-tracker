# File: outreach_engine/api/analytics_api.py

from fastapi import APIRouter, HTTPException

from outreach_engine.analytics.dashboard_data import (
    get_campaign_dashboard,
    get_all_campaigns_dashboard
)

from outreach_engine.analytics.funnel_analysis import followup_effectiveness
from outreach_engine.analytics.revenue_analytics import (
    get_campaign_revenue,
    weekly_revenue
)

from outreach_engine.analytics.ml_revenue_model import predict_revenue_ml
from outreach_engine.analytics.send_time_predictor import predict_reply_probability

from outreach_engine.database.supabase_client import supabase

router = APIRouter()


# ---------------------------------------------------
# 📊 DASHBOARD ENDPOINTS
# ---------------------------------------------------

@router.get("/dashboard")
def dashboard():
    try:
        return {
            "status": "success",
            "data": get_all_campaigns_dashboard()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/campaign/{campaign_id}")
def dashboard_campaign(campaign_id: int):
    try:
        return {
            "status": "success",
            "campaign_id": campaign_id,
            "data": get_campaign_dashboard(campaign_id)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------
# 📈 CRM ANALYTICS
# ---------------------------------------------------

@router.get("/crm/{lead_id}")
def crm_analytics(lead_id: int):
    try:
        result = (
            supabase.table("crm_analytics")
            .select("*")
            .eq("lead_id", lead_id)
            .execute()
        )

        if result.data:
            return {
                "status": "success",
                "data": result.data[0]
            }

        return {"status": "error", "message": "Lead analytics not found"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------
# 🔥 FUNNEL ANALYSIS
# ---------------------------------------------------

@router.get("/campaign/{campaign_id}/funnel")
def campaign_funnel(campaign_id: int):
    try:
        funnel = followup_effectiveness(campaign_id)

        return {
            "status": "success",
            "campaign_id": campaign_id,
            "funnel": funnel
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------
# 💰 REVENUE ANALYTICS
# ---------------------------------------------------

@router.get("/campaign/{campaign_id}/revenue")
def campaign_revenue(campaign_id: int):
    try:
        revenue = get_campaign_revenue(campaign_id)

        return {
            "status": "success",
            "campaign_id": campaign_id,
            "revenue": revenue
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaign/{campaign_id}/revenue/weekly")
def campaign_weekly_revenue(campaign_id: int):
    try:
        revenue = weekly_revenue(campaign_id)

        return {
            "status": "success",
            "campaign_id": campaign_id,
            "weekly_revenue": revenue
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------
# 🧠 AI INSIGHTS (ULTRA CORE)
# ---------------------------------------------------

@router.get("/campaign/{campaign_id}/ai-insights")
def campaign_ai_insights(campaign_id: int):
    """
    🔥 AI-powered insights:
    - reply probability
    - ML revenue prediction
    - expected revenue ranking
    """

    try:
        leads = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
            .data
        )

        insights = []

        for lead in leads:
            reply_prob = predict_reply_probability(lead)
            ml_revenue = predict_revenue_ml(lead)

            expected_revenue = reply_prob * ml_revenue

            insights.append({
                "lead_id": lead["id"],
                "email": lead.get("email"),
                "reply_probability": round(reply_prob, 3),
                "ml_predicted_revenue": round(ml_revenue, 2),
                "expected_revenue": round(expected_revenue, 2)
            })

        # 🔥 SMART SORTING
        insights = sorted(
            insights,
            key=lambda x: x["expected_revenue"],
            reverse=True
        )

        return {
            "status": "success",
            "campaign_id": campaign_id,
            "total_leads": len(insights),
            "top_opportunities": insights[:10],
            "all_leads_ranked": insights
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------
# 🚀 TOP LEADS (Quick Access Endpoint)
# ---------------------------------------------------

@router.get("/campaign/{campaign_id}/top-leads")
def top_leads(campaign_id: int):
    """
    Fast endpoint for frontend dashboards
    """

    try:
        leads = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
            .data
        )

        enriched = []

        for lead in leads:
            prob = predict_reply_probability(lead)
            revenue = predict_revenue_ml(lead)

            enriched.append({
                "lead_id": lead["id"],
                "email": lead.get("email"),
                "priority_score": round(prob * revenue, 2)
            })

        enriched = sorted(
            enriched,
            key=lambda x: x["priority_score"],
            reverse=True
        )

        return {
            "status": "success",
            "campaign_id": campaign_id,
            "top_leads": enriched[:10]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))