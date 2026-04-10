# outreach_engine/api/dashboard_api.py

from typing import Dict, Any, Optional

from fastapi import FastAPI, APIRouter, Query

from outreach_engine.database.supabase_client import supabase

app = FastAPI(title="Outreach Dashboard API")
router = APIRouter()


def safe_int(val) -> int:
    try:
        return int(val or 0)
    except Exception:
        return 0


def safe_float(val) -> float:
    try:
        return float(val or 0)
    except Exception:
        return 0.0


def _normalize_channel(channel: Optional[str]) -> str:
    return (channel or "").strip().lower()


def _get_campaign_name(campaign_id: int) -> str:
    try:
        res = (
            supabase
            .table("campaigns")
            .select("name")
            .eq("id", campaign_id)
            .limit(1)
            .execute()
        )
        if res.data and len(res.data) > 0:
            return res.data[0].get("name") or f"Campaign {campaign_id}"
    except Exception as e:
        print(f"⚠ Failed to fetch campaign name: {e}")

    return f"Campaign {campaign_id}"


def _lead_matches_channel(lead: Dict[str, Any], channel: str) -> bool:
    """
    If channel filter is used, try to match it against metadata.channel.
    If no channel is provided, include all leads.
    """
    if not channel:
        return True

    metadata = lead.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False

    lead_channel = _normalize_channel(metadata.get("channel"))
    return lead_channel == channel


def _build_dashboard_payload(campaign_id: int, channel: str = "") -> Dict[str, Any]:
    campaign_name = _get_campaign_name(campaign_id)
    channel = _normalize_channel(channel)

    res = (
        supabase
        .table("outreach_leads")
        .select("*")
        .eq("campaign_id", campaign_id)
        .execute()
    )

    leads = res.data or []
    filtered_leads = [lead for lead in leads if _lead_matches_channel(lead, channel)]

    total_leads = len(filtered_leads)

    emails_sent = len([
        l for l in filtered_leads
        if (l.get("status") or "").lower() in ["sent", "replied", "converted"]
    ])

    opens = sum(safe_int(l.get("open_count")) for l in filtered_leads)
    clicks = sum(safe_int(l.get("click_count")) for l in filtered_leads)
    replies = sum(safe_int(l.get("reply_count")) for l in filtered_leads)
    conversions = sum(safe_int(l.get("conversion_count")) for l in filtered_leads)

    open_rate = (opens / emails_sent) if emails_sent else 0
    click_rate = (clicks / emails_sent) if emails_sent else 0
    reply_rate = (replies / emails_sent) if emails_sent else 0
    conversion_rate = (conversions / emails_sent) if emails_sent else 0

    recommendations = []
    if emails_sent > 0:
        if open_rate < 0.3:
            recommendations.append("Low open rate → improve subject lines")
        if reply_rate < 0.1:
            recommendations.append("Low reply rate → improve email body / CTA")
        if conversion_rate < 0.05:
            recommendations.append("Low conversion → improve offer / landing")

    return {
        "campaign_id": campaign_id,
        "campaign_name": campaign_name,
        "channel": channel or "all",
        "total_leads": total_leads,
        "emails_sent": emails_sent,
        "opens": opens,
        "clicks": clicks,
        "replies": replies,
        "conversions": conversions,
        "open_rate": round(open_rate, 3),
        "click_rate": round(click_rate, 3),
        "reply_rate": round(reply_rate, 3),
        "conversion_rate": round(conversion_rate, 3),
        "recommendations": recommendations,
    }


@router.get("/dashboard/campaigns/{campaign_id}")
def get_campaign_dashboard(
    campaign_id: int,
    channel: str = Query(default="")
) -> Dict[str, Any]:
    """
    Returns dashboard metrics for one campaign.
    Supports optional channel filter:
    - email
    - sms
    - linkedin
    - call
    """
    try:
        print(f"📊 Fetching campaign {campaign_id} | channel={channel or 'all'}")
        payload = _build_dashboard_payload(campaign_id, channel)
        print(
            f"✅ Dashboard ready | leads={payload['total_leads']} | "
            f"sent={payload['emails_sent']} | opens={payload['opens']}"
        )
        return payload

    except Exception as e:
        print(f"⚠ Dashboard error: {e}")
        return {
            "campaign_id": campaign_id,
            "campaign_name": f"Campaign {campaign_id}",
            "channel": channel or "all",
            "total_leads": 0,
            "emails_sent": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
            "open_rate": 0,
            "click_rate": 0,
            "reply_rate": 0,
            "conversion_rate": 0,
            "recommendations": [],
            "error": str(e),
        }


@router.get("/dashboard/campaigns")
def get_all_campaign_dashboards(channel: str = Query(default="")) -> Dict[str, Any]:
    """
    Returns all campaign dashboards.
    """
    try:
        campaigns_res = (
            supabase
            .table("campaigns")
            .select("id")
            .execute()
        )

        campaigns = campaigns_res.data or []
        data = [
            _build_dashboard_payload(c["id"], channel)
            for c in campaigns
        ]

        return {
            "channel": _normalize_channel(channel) or "all",
            "count": len(data),
            "data": data,
        }

    except Exception as e:
        print(f"⚠ All dashboards error: {e}")
        return {
            "channel": _normalize_channel(channel) or "all",
            "count": 0,
            "data": [],
            "error": str(e),
        }


@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


app.include_router(router)