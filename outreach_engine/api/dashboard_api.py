# outreach_engine/api/dashboard_api.py

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import (
    get_campaign_events,
    get_campaign_funnel,
    get_campaign_metrics,
)

router = APIRouter(tags=["Dashboard"])


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _percent(part: int, whole: int) -> float:
    return round((part / whole) * 100, 1) if whole else 0.0


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _get_campaign_name(campaign_id: int) -> str:
    try:
        res = (
            supabase.table("campaigns")
            .select("name, campaign_name")
            .eq("id", campaign_id)
            .limit(1)
            .execute()
        )
        if res.data:
            row = res.data[0]
            name = row.get("campaign_name") or row.get("name")
            if name:
                return str(name)
    except Exception:
        pass

    return f"Campaign {campaign_id}"


def _get_latest_campaign_id() -> Optional[int]:
    try:
        res = (
            supabase.table("campaigns")
            .select("id")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            cid = res.data[0].get("id")
            if cid is not None:
                return int(cid)
    except Exception:
        pass

    try:
        res = (
            supabase.table("outreach_leads")
            .select("campaign_id")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            cid = res.data[0].get("campaign_id")
            if cid is not None:
                return int(cid)
    except Exception:
        pass

    return None


def _get_campaigns() -> List[Dict[str, Any]]:
    try:
        res = (
            supabase.table("campaigns")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        rows = res.data or []
        cleaned: List[Dict[str, Any]] = []

        for row in rows:
            cleaned.append(
                {
                    "id": row.get("id"),
                    "name": row.get("campaign_name") or row.get("name") or f"Campaign {row.get('id')}",
                    "campaign_name": row.get("campaign_name") or row.get("name") or f"Campaign {row.get('id')}",
                    "created_at": row.get("created_at"),
                    "status": row.get("status"),
                }
            )

        return cleaned
    except Exception:
        return []


def _get_campaign_leads(campaign_id: int, last_days: int = 7) -> List[Dict[str, Any]]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
        )
        leads = res.data or []
    except Exception:
        leads = []

    if last_days and last_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=last_days)
        filtered: List[Dict[str, Any]] = []

        for lead in leads:
            created_at = _parse_iso(lead.get("created_at"))
            if created_at is None:
                continue
            if created_at >= cutoff:
                filtered.append(lead)

        return filtered

    return leads


def _count_sent_from_leads(leads: List[Dict[str, Any]]) -> int:
    sent_statuses = {"sent", "replied", "converted"}
    count = 0

    for lead in leads:
        status = str(lead.get("status") or "").strip().lower()
        if lead.get("last_email_sent") or status in sent_statuses:
            count += 1

    return count


def _count_replied_from_leads(leads: List[Dict[str, Any]]) -> int:
    count = 0

    for lead in leads:
        status = str(lead.get("status") or "").strip().lower()
        if (
            _safe_int(lead.get("reply_count")) > 0
            or status == "replied"
            or bool(lead.get("reply_status"))
        ):
            count += 1

    return count


def _count_converted_from_leads(leads: List[Dict[str, Any]]) -> int:
    count = 0

    for lead in leads:
        status = str(lead.get("status") or "").strip().lower()
        if _safe_int(lead.get("conversion_count")) > 0 or status == "converted":
            count += 1

    return count


def _build_followup_steps(leads: List[Dict[str, Any]]) -> Dict[str, int]:
    steps: Dict[str, int] = {}
    for lead in leads:
        step = str(lead.get("followup_step") or 0)
        steps[step] = steps.get(step, 0) + 1
    return steps


def _build_dashboard(campaign_id: int, channel: str = "all", last_days: int = 7) -> Dict[str, Any]:
    leads = _get_campaign_leads(campaign_id, last_days=last_days)

    # Event-driven metrics first
    try:
        metrics = get_campaign_metrics(campaign_id, last_days=last_days)
    except Exception:
        metrics = {}

    try:
        funnel = get_campaign_funnel(campaign_id, last_days=last_days)
    except Exception:
        funnel = {}

    total_leads = len(leads)

    emails_sent = _safe_int(metrics.get("emails_sent"))
    opens = _safe_int(metrics.get("opens"))
    clicks = _safe_int(metrics.get("clicks"))
    replies = _safe_int(metrics.get("replies"))
    conversions = _safe_int(metrics.get("conversions"))

    # Backward-compatible fallback to lead fields
    if emails_sent == 0:
        emails_sent = _count_sent_from_leads(leads)
    if replies == 0:
        replies = _count_replied_from_leads(leads)
    if conversions == 0:
        conversions = _count_converted_from_leads(leads) if "_count_converted_from_leads" in globals() else sum(
            1 for lead in leads if str(lead.get("status") or "").strip().lower() == "converted"
        )
    if opens == 0:
        opens = sum(_safe_int(lead.get("open_count")) for lead in leads)
    if clicks == 0:
        clicks = sum(_safe_int(lead.get("click_count")) for lead in leads)

    open_rate = _percent(opens, emails_sent)
    click_rate = _percent(clicks, emails_sent)
    reply_rate = _percent(replies, emails_sent)
    conversion_rate = _percent(conversions, emails_sent)

    if not funnel:
        funnel = {
            "total_sent": emails_sent,
            "replied": replies,
            "converted": conversions,
            "drop_off_to_reply_pct": round(100 - _percent(replies, emails_sent), 1) if emails_sent else 0.0,
            "drop_off_to_conversion_pct": round(100 - _percent(conversions, replies), 1) if replies else 0.0,
        }

    recommendations: List[str] = []
    if emails_sent == 0:
        recommendations.append("No emails sent yet")
    if emails_sent > 0 and opens == 0:
        recommendations.append("Improve subject line or deliverability")
    if emails_sent > 0 and replies == 0:
        recommendations.append("Improve CTA / personalization")
    if replies > 0 and conversions == 0:
        recommendations.append("Add a clearer CTA or stronger offer")

    followup_steps = _build_followup_steps(leads)

    return {
        "campaign_id": campaign_id,
        "campaign_name": _get_campaign_name(campaign_id),
        "channel": channel or "all",
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
        "open_rate": open_rate,
        "click_rate": click_rate,
        "reply_rate": reply_rate,
        "conversion_rate": conversion_rate,
        "funnel": {
            "total_sent": _safe_int(funnel.get("total_sent", emails_sent)),
            "replied": _safe_int(funnel.get("replied", replies)),
            "converted": _safe_int(funnel.get("converted", conversions)),
            "drop_off_to_reply_pct": float(funnel.get("drop_off_to_reply_pct", round(100 - _percent(replies, emails_sent), 1) if emails_sent else 0.0)),
            "drop_off_to_conversion_pct": float(funnel.get("drop_off_to_conversion_pct", round(100 - _percent(conversions, replies), 1) if replies else 0.0)),
        },
        "followup_steps": followup_steps,
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
            "open_rate": open_rate,
            "click_rate": click_rate,
            "reply_rate": reply_rate,
            "conversion_rate": conversion_rate,
        },
        "total_expected_revenue": 0,
        "avg_expected_revenue": 0,
    }


def _empty_dashboard(campaign_id: Optional[int] = None) -> Dict[str, Any]:
    return {
        "campaign_id": campaign_id,
        "campaign_name": "Unknown Campaign",
        "channel": "all",
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
        "open_rate": 0.0,
        "click_rate": 0.0,
        "reply_rate": 0.0,
        "conversion_rate": 0.0,
        "funnel": {
            "total_sent": 0,
            "replied": 0,
            "converted": 0,
            "drop_off_to_reply_pct": 0.0,
            "drop_off_to_conversion_pct": 0.0,
        },
        "followup_steps": {},
        "recommendations": [],
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
            "open_rate": 0.0,
            "click_rate": 0.0,
            "reply_rate": 0.0,
            "conversion_rate": 0.0,
        },
        "total_expected_revenue": 0,
        "avg_expected_revenue": 0,
    }


@router.get("/dashboard")
def dashboard(
    campaign_id: Optional[int] = Query(default=None),
    channel: str = Query(default="all"),
    last_days: int = Query(default=7),
) -> Dict[str, Any]:
    try:
        resolved_campaign_id = campaign_id or _get_latest_campaign_id()
        if resolved_campaign_id is None:
            return _empty_dashboard(None)

        return _build_dashboard(
            campaign_id=resolved_campaign_id,
            channel=channel,
            last_days=last_days,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/campaigns/{campaign_id}")
def campaign_dashboard(campaign_id: int, channel: str = Query(default="all"), last_days: int = Query(default=7)) -> Dict[str, Any]:
    try:
        return _build_dashboard(campaign_id=campaign_id, channel=channel, last_days=last_days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/campaign/{campaign_id}")
def campaign_dashboard_alias(campaign_id: int, channel: str = Query(default="all"), last_days: int = Query(default=7)) -> Dict[str, Any]:
    try:
        return _build_dashboard(campaign_id=campaign_id, channel=channel, last_days=last_days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/campaigns")
def all_campaigns_dashboard(channel: str = Query(default="all"), last_days: int = Query(default=7)) -> Dict[str, Any]:
    try:
        campaigns = _get_campaigns()
        data = [
            _build_dashboard(int(c["id"]), channel=channel, last_days=last_days)
            for c in campaigns
            if c.get("id") is not None
        ]
        return {
            "status": "success",
            "count": len(data),
            "data": data,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaigns")
def list_campaigns() -> List[Dict[str, Any]]:
    try:
        campaigns = _get_campaigns()
        if campaigns:
            return campaigns

        # fallback if campaigns table is empty
        res = supabase.table("outreach_leads").select("campaign_id").execute()
        ids = []
        for row in res.data or []:
            cid = row.get("campaign_id")
            if cid is not None:
                try:
                    ids.append(int(cid))
                except Exception:
                    continue

        unique_ids = sorted(set(ids), reverse=True)
        return [
            {
                "id": cid,
                "name": f"Campaign {cid}",
                "campaign_name": f"Campaign {cid}",
                "created_at": None,
                "status": None,
            }
            for cid in unique_ids
        ]
    except Exception:
        return []


@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}