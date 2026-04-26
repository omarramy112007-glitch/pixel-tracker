# outreach_engine/api/analytics_api.py

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from outreach_engine.analytics.dashboard_data import (
    get_campaign_dashboard,
    get_all_campaigns_dashboard,
)
from outreach_engine.analytics.funnel_analysis import followup_effectiveness
from outreach_engine.analytics.revenue_analytics import (
    get_campaign_revenue,
    weekly_revenue,
)
from outreach_engine.analytics.ml_revenue_model import predict_revenue_ml
from outreach_engine.analytics.send_time_predictor import predict_reply_probability
from outreach_engine.tracking.event_repository import get_lead_events
from outreach_engine.database.supabase_client import supabase

router = APIRouter(tags=["Analytics"])


# --------------------------------------------------
# OVERVIEW
# --------------------------------------------------

@router.get("/overview")
def overview():
    try:
        return {
            "status": "success",
            "data": get_all_campaigns_dashboard(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaign/{campaign_id}")
def campaign_overview(campaign_id: int, channel: str = ""):
    """
    Lightweight campaign summary endpoint for analytics consumers.
    """
    try:
        return {
            "status": "success",
            "campaign_id": campaign_id,
            "data": get_campaign_dashboard(campaign_id, channel=channel),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --------------------------------------------------
# CRM ANALYTICS (EVENT-BASED FALLBACK)
# --------------------------------------------------

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
                "data": result.data[0],
                "source": "crm_table",
            }

        events = get_lead_events(lead_id)

        metrics = {
            "emails_sent": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
        }

        for e in events:
            et = (e.get("event_type") or "").lower().strip()

            if et in {"sent", "email_sent"}:
                metrics["emails_sent"] += 1
            elif et in {"opened", "open", "email_opened", "pixel_open"}:
                metrics["opens"] += 1
            elif et in {"clicked", "click", "link_clicked"}:
                metrics["clicks"] += 1
            elif et in {"replied", "reply", "email_replied"}:
                metrics["replies"] += 1
            elif et in {"converted", "convert", "conversion"}:
                metrics["conversions"] += 1

        return {
            "status": "success",
            "data": metrics,
            "source": "event_fallback",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --------------------------------------------------
# FUNNEL ANALYSIS
# --------------------------------------------------

@router.get("/campaign/{campaign_id}/funnel")
def campaign_funnel(campaign_id: int):
    try:
        funnel = followup_effectiveness(campaign_id)
        return {
            "status": "success",
            "campaign_id": campaign_id,
            "funnel": funnel,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --------------------------------------------------
# REVENUE ANALYTICS
# --------------------------------------------------

@router.get("/campaign/{campaign_id}/revenue")
def campaign_revenue(campaign_id: int):
    try:
        revenue = get_campaign_revenue(campaign_id)
        return {
            "status": "success",
            "campaign_id": campaign_id,
            "revenue": revenue,
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
            "weekly_revenue": revenue,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --------------------------------------------------
# AI INSIGHTS
# --------------------------------------------------

@router.get("/campaign/{campaign_id}/ai-insights")
@router.get("/campaign/{campaign_id}/optimize")
def campaign_ai_insights(campaign_id: int):
    """
    Returns:
    - simple insight flags
    - recommended actions
    - ranked opportunities
    """
    try:
        dashboard = get_campaign_dashboard(campaign_id)

        leads = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
            .data
            or []
        )

        insights = []
        if dashboard.get("open_rate", 0) < 0.2:
            insights.append("Low open rate detected")
        if dashboard.get("click_rate", 0) < 0.1:
            insights.append("Low click rate")
        if dashboard.get("reply_rate", 0) < 0.1:
            insights.append("Low reply rate")
        if dashboard.get("conversion_rate", 0) < 0.05:
            insights.append("Low conversion rate")

        funnel = dashboard.get("funnel") or {}
        if funnel.get("drop_off_to_reply_pct", 0) > 70:
            insights.append("Major drop-off before replies")
        if funnel.get("drop_off_to_conversion_pct", 0) > 60:
            insights.append("Drop-off after replies")

        opportunities = []
        for lead in leads:
            reply_prob = predict_reply_probability(lead)
            ml_revenue = predict_revenue_ml(lead)
            expected_revenue = reply_prob * ml_revenue

            opportunities.append({
                "lead_id": lead["id"],
                "email": lead.get("email"),
                "reply_probability": round(reply_prob, 3),
                "ml_predicted_revenue": round(ml_revenue, 2),
                "expected_revenue": round(expected_revenue, 2),
            })

        opportunities = sorted(
            opportunities,
            key=lambda x: x["expected_revenue"],
            reverse=True,
        )

        return {
            "status": "success",
            "campaign_id": campaign_id,
            "insights": insights,
            "recommended_actions": dashboard.get("recommendations", []),
            "total_leads": len(opportunities),
            "top_opportunities": opportunities[:10],
            "all_leads_ranked": opportunities,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --------------------------------------------------
# TOP LEADS
# --------------------------------------------------

@router.get("/campaign/{campaign_id}/top-leads")
def top_leads(campaign_id: int):
    try:
        leads = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
            .data
            or []
        )

        enriched = []
        for lead in leads:
            prob = predict_reply_probability(lead)
            revenue = predict_revenue_ml(lead)
            enriched.append({
                "lead_id": lead["id"],
                "email": lead.get("email"),
                "priority_score": round(prob * revenue, 2),
            })

        enriched = sorted(
            enriched,
            key=lambda x: x["priority_score"],
            reverse=True,
        )

        return {
            "status": "success",
            "campaign_id": campaign_id,
            "top_leads": enriched[:10],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))