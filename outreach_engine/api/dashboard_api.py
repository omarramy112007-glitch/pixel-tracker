# outreach_engine/api/dashboard_api.py

from __future__ import annotations

from typing import Dict, Any, Optional, List

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
            supabase.table("campaigns")
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


def _get_campaign_lead_ids(campaign_id: int) -> List[str]:
    """
    Get all lead IDs assigned to a campaign from campaign_leads.
    This is the clean link between campaign and crm_analytics.
    """
    try:
        res = (
            supabase.table("campaign_leads")
            .select("lead_id")
            .eq("campaign_id", campaign_id)
            .execute()
        )
        rows = res.data or []
        return [str(r["lead_id"]) for r in rows if r.get("lead_id")]
    except Exception as e:
        print(f"⚠ Failed to fetch campaign lead IDs: {e}")
        return []


def _fetch_crm_rows(lead_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Fetch crm_analytics rows for a set of lead IDs.
    """
    if not lead_ids:
        return []

    try:
        res = (
            supabase.table("crm_analytics")
            .select("*")
            .in_("lead_id", lead_ids)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"⚠ Failed to fetch crm_analytics rows: {e}")
        return []


def _aggregate_crm_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate metrics directly from crm_analytics.
    """
    emails_sent = sum(safe_int(r.get("emails_sent")) for r in rows)
    opens = sum(safe_int(r.get("opens")) for r in rows)
    clicks = sum(safe_int(r.get("clicks")) for r in rows)
    replies = sum(safe_int(r.get("replies")) for r in rows)
    conversions = sum(safe_int(r.get("conversions")) for r in rows)

    open_rate = (opens / emails_sent) if emails_sent else 0.0
    click_rate = (clicks / emails_sent) if emails_sent else 0.0
    reply_rate = (replies / emails_sent) if emails_sent else 0.0
    conversion_rate = (conversions / emails_sent) if emails_sent else 0.0

    recommendations: List[str] = []
    if emails_sent > 0:
        if open_rate < 0.3:
            recommendations.append("Low open rate → improve subject lines")
        if reply_rate < 0.1:
            recommendations.append("Low reply rate → improve email body / CTA")
        if conversion_rate < 0.05:
            recommendations.append("Low conversion → improve offer / landing")

    return {
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


def _fallback_from_outreach_leads(campaign_id: int) -> Dict[str, Any]:
    """
    Legacy fallback in case campaign_leads/crm_analytics are not populated yet.
    This should only be a safety net, not the primary source.
    """
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
        )
        leads = res.data or []

        emails_sent = len(
            [
                l for l in leads
                if (l.get("status") or "").lower() in {"sent", "replied", "converted"}
            ]
        )
        opens = sum(safe_int(l.get("open_count")) for l in leads)
        clicks = sum(safe_int(l.get("click_count")) for l in leads)
        replies = sum(safe_int(l.get("reply_count")) for l in leads)
        conversions = sum(safe_int(l.get("conversion_count")) for l in leads)

        open_rate = (opens / emails_sent) if emails_sent else 0.0
        click_rate = (clicks / emails_sent) if emails_sent else 0.0
        reply_rate = (replies / emails_sent) if emails_sent else 0.0
        conversion_rate = (conversions / emails_sent) if emails_sent else 0.0

        return {
            "emails_sent": emails_sent,
            "opens": opens,
            "clicks": clicks,
            "replies": replies,
            "conversions": conversions,
            "open_rate": round(open_rate, 3),
            "click_rate": round(click_rate, 3),
            "reply_rate": round(reply_rate, 3),
            "conversion_rate": round(conversion_rate, 3),
            "recommendations": [],
        }
    except Exception as e:
        print(f"⚠ Legacy fallback failed: {e}")
        return {
            "emails_sent": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
            "open_rate": 0.0,
            "click_rate": 0.0,
            "reply_rate": 0.0,
            "conversion_rate": 0.0,
            "recommendations": [],
        }


def _build_dashboard_payload(campaign_id: int, channel: str = "") -> Dict[str, Any]:
    campaign_name = _get_campaign_name(campaign_id)
    channel = _normalize_channel(channel)

    # Primary source of truth:
    # campaign_leads -> crm_analytics
    lead_ids = _get_campaign_lead_ids(campaign_id)
    crm_rows = _fetch_crm_rows(lead_ids)

    if crm_rows:
        metrics = _aggregate_crm_metrics(crm_rows)
        total_leads = len(crm_rows)
    else:
        # Safety fallback only if crm_analytics is not yet populated
        metrics = _fallback_from_outreach_leads(campaign_id)
        total_leads = metrics.get("emails_sent", 0)

    payload = {
        "campaign_id": campaign_id,
        "campaign_name": campaign_name,
        "channel": channel or "all",
        "total_leads": total_leads,
        "emails_sent": metrics["emails_sent"],
        "sms_sent": 0,
        "linkedin_sent": 0,
        "calls_made": 0,
        "consulting_leads": 0,
        "calls_booked": 0,
        "consulting_converted": 0,
        "opens": metrics["opens"],
        "clicks": metrics["clicks"],
        "replies": metrics["replies"],
        "conversions": metrics["conversions"],
        "open_rate": metrics["open_rate"],
        "click_rate": metrics["click_rate"],
        "reply_rate": metrics["reply_rate"],
        "conversion_rate": metrics["conversion_rate"],
        "recommendations": metrics["recommendations"],
        "metrics": {
            "emails_sent": metrics["emails_sent"],
            "sms_sent": 0,
            "linkedin_sent": 0,
            "calls_made": 0,
            "opens": metrics["opens"],
            "clicks": metrics["clicks"],
            "replies": metrics["replies"],
            "conversions": metrics["conversions"],
            "open_rate": metrics["open_rate"],
            "click_rate": metrics["click_rate"],
            "reply_rate": metrics["reply_rate"],
            "conversion_rate": metrics["conversion_rate"],
        },
    }

    return payload


@router.get("/dashboard/campaigns/{campaign_id}")
def get_campaign_dashboard(
    campaign_id: int,
    channel: str = Query(default=""),
) -> Dict[str, Any]:
    """
    Returns dashboard metrics for one campaign.
    Supports optional channel filter.
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
            "error": str(e),
        }


@router.get("/dashboard/campaigns")
def get_all_campaign_dashboards(channel: str = Query(default="")) -> Dict[str, Any]:
    """
    Returns all campaign dashboards.
    """
    try:
        campaigns_res = (
            supabase.table("campaigns")
            .select("id")
            .execute()
        )

        campaigns = campaigns_res.data or []
        data = [
            _build_dashboard_payload(c["id"], channel)
            for c in campaigns
            if c.get("id") is not None
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


@router.get("/dashboard")
def dashboard_root(channel: str = Query(default="")) -> Dict[str, Any]:
    """
    Alias for /dashboard/campaigns so curl http://localhost:8000/dashboard works.
    """
    return get_all_campaign_dashboards(channel=channel)


@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


app.include_router(router)