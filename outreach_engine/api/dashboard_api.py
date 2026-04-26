# File: outreach_engine/api/dashboard_api.py

from __future__ import annotations

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from fastapi import APIRouter, FastAPI, Query, HTTPException

from outreach_engine.analytics.dashboard_data import (
    get_campaign_dashboard,
    get_all_campaigns_dashboard,
)
from outreach_engine.database.supabase_client import supabase

router = APIRouter(tags=["Dashboard"])

app = FastAPI(title="Outreach Dashboard API")
app.include_router(router, prefix="/analytics")


def _get_campaign_name(campaign_id: int) -> str:
    try:
        res = (
            supabase.table("campaigns")
            .select("name")
            .eq("id", campaign_id)
            .limit(1)
            .execute()
        )
        if res.data:
            name = res.data[0].get("name")
            if name:
                return str(name)
    except Exception:
        pass
    return f"Campaign {campaign_id}"


def _query_outreach_leads(campaign_id: int, last_days: int = 7) -> List[Dict[str, Any]]:
    try:
        cutoff = datetime.utcnow() - timedelta(days=last_days)

        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("campaign_id", campaign_id)
            .gte("created_at", cutoff.isoformat())
            .execute()
        )
        return res.data or []
    except Exception:
        return []


def _build_fallback_dashboard(campaign_id: int, channel: str = "", last_days: int = 7) -> Dict[str, Any]:
    leads = _query_outreach_leads(campaign_id, last_days=last_days)

    if channel:
        channel = channel.lower().strip()

    if channel and channel != "all":
        filtered = []
        for lead in leads:
            meta = lead.get("metadata") or {}
            lead_channel = str(meta.get("channel") or meta.get("source_channel") or "").lower()
            if lead_channel == channel:
                filtered.append(lead)
        if filtered:
            leads = filtered

    total_leads = len(leads)
    emails_sent = sum(1 for l in leads if l.get("last_email_sent") or l.get("status") in {"sent", "replied", "converted"})
    opens = sum(int(l.get("open_count") or 0) for l in leads)
    clicks = sum(int(l.get("click_count") or 0) for l in leads)
    replies = sum(int(l.get("reply_count") or 0) for l in leads)
    conversions = sum(int(l.get("conversion_count") or 0) for l in leads)

    def pct(part: int, whole: int) -> float:
        return round((part / whole) * 100, 1) if whole else 0.0

    funnel = {
        "total_sent": emails_sent,
        "replied": replies,
        "converted": conversions,
        "drop_off_to_reply_pct": round(100 - pct(replies, emails_sent), 1) if emails_sent else 0.0,
        "drop_off_to_conversion_pct": round(100 - pct(conversions, emails_sent), 1) if emails_sent else 0.0,
    }

    recommendations = []
    if emails_sent == 0:
        recommendations.append("No emails sent yet")
    if opens == 0 and emails_sent > 0:
        recommendations.append("Improve subject line or deliverability")
    if replies == 0 and emails_sent > 0:
        recommendations.append("Improve CTA / personalization")
    if conversions == 0 and replies > 0:
        recommendations.append("Add a clearer CTA or stronger offer")

    return {
        "campaign_id": campaign_id,
        "campaign_name": _get_campaign_name(campaign_id),
        "total_leads": total_leads,
        "emails_sent": emails_sent,
        "sms_sent": 0,
        "linkedin_sent": 0,
        "calls_made": 0,
        "consulting_leads": 0,
        "calls_booked": 0,
        "consulting_converted": 0,
        "opens": opens,
        "clicks": clicks,
        "replies": replies,
        "conversions": conversions,
        "open_rate": pct(opens, emails_sent),
        "click_rate": pct(clicks, emails_sent),
        "reply_rate": pct(replies, emails_sent),
        "conversion_rate": pct(conversions, emails_sent),
        "funnel": funnel,
        "followup_steps": {},
        "recommendations": recommendations,
        "total_events": opens + clicks + replies + conversions,
        "metrics": {
            "emails_sent": emails_sent,
            "sms_sent": 0,
            "linkedin_sent": 0,
            "calls_made": 0,
            "opens": opens,
            "clicks": clicks,
            "replies": replies,
            "conversions": conversions,
            "open_rate": pct(opens, emails_sent),
            "click_rate": pct(clicks, emails_sent),
            "reply_rate": pct(replies, emails_sent),
            "conversion_rate": pct(conversions, emails_sent),
        },
        "total_expected_revenue": 0,
        "avg_expected_revenue": 0,
    }


def _empty_dashboard(campaign_id: Optional[int] = None) -> Dict[str, Any]:
    return {
        "campaign_id": campaign_id,
        "campaign_name": "Unknown Campaign",
        "total_leads": 0,
        "emails_sent": 0,
        "sms_sent": 0,
        "linkedin_sent": 0,
        "calls_made": 0,
        "consulting_leads": 0,
        "calls_booked": 0,
        "consulting_converted": 0,
        "opens": 0,
        "clicks": 0,
        "replies": 0,
        "conversions": 0,
        "open_rate": 0,
        "click_rate": 0,
        "reply_rate": 0,
        "conversion_rate": 0,
        "recommendations": [],
        "total_expected_revenue": 0,
        "avg_expected_revenue": 0,
        "funnel": {
            "total_sent": 0,
            "replied": 0,
            "converted": 0,
            "drop_off_to_reply_pct": 0,
            "drop_off_to_conversion_pct": 0,
        },
        "followup_steps": {},
        "total_events": 0,
        "metrics": {
            "emails_sent": 0,
            "sms_sent": 0,
            "linkedin_sent": 0,
            "calls_made": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
            "open_rate": 0,
            "click_rate": 0,
            "reply_rate": 0,
            "conversion_rate": 0,
        },
    }


@router.get("/dashboard/campaigns/{campaign_id}")
def campaign_dashboard(campaign_id: int, channel: str = Query(default="")) -> Dict[str, Any]:
    try:
        data = get_campaign_dashboard(campaign_id, channel=channel)
        if data:
            return data
        return _build_fallback_dashboard(campaign_id, channel=channel)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/campaign/{campaign_id}")
def campaign_dashboard_alias(campaign_id: int, channel: str = Query(default="")) -> Dict[str, Any]:
    try:
        data = get_campaign_dashboard(campaign_id, channel=channel)
        if data:
            return data
        return _build_fallback_dashboard(campaign_id, channel=channel)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/campaigns")
def all_campaigns_dashboard(channel: str = Query(default="")) -> Dict[str, Any]:
    try:
        data = get_all_campaigns_dashboard(channel=channel)
        return {
            "status": "success",
            "count": len(data),
            "data": data,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard")
def dashboard(
    campaign_id: Optional[int] = Query(default=1),
    channel: str = Query(default=""),
    last_days: int = Query(default=7),
) -> Dict[str, Any]:
    try:
        resolved_campaign_id = campaign_id or 1

        data = get_campaign_dashboard(resolved_campaign_id, channel=channel)
        if data:
            return data

        return _build_fallback_dashboard(resolved_campaign_id, channel=channel, last_days=last_days)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("outreach_engine.api.dashboard_api:app", host="0.0.0.0", port=8001, reload=True)