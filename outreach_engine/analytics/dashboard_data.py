# File: outreach_engine/analytics/dashboard_data.py

from typing import Dict, Any, List
from functools import lru_cache

from outreach_engine.database.supabase_client import supabase
from outreach_engine.tracking.event_repository import get_campaign_events
from outreach_engine.analytics.funnel_analysis import followup_effectiveness


@lru_cache(maxsize=128)
def _get_campaign_name(campaign_id: int) -> str:
    try:
        res = (
            supabase.table("campaigns")
            .select("name")
            .eq("id", campaign_id)
            .limit(1)
            .execute()
        )
        return res.data[0]["name"] if res.data else f"Campaign {campaign_id}"
    except Exception:
        return f"Campaign {campaign_id}"


def _normalize_event_type(et: str) -> str:
    et = (et or "").lower().strip()

    if "_" in et:
        prefix, suffix = et.split("_", 1)
        if prefix in {"email", "sms", "linkedin", "call"}:
            et = suffix

    if et in ["open", "opened", "pixel_open", "email_opened"]:
        return "open"
    if et in ["click", "clicked", "link_clicked"]:
        return "click"
    if et in ["reply", "replied", "email_replied"]:
        return "reply"
    if et in ["sent", "email_sent", "send"]:
        return "sent"
    if et in ["convert", "converted", "conversion", "deal_closed"]:
        return "convert"

    return et


def _event_matches_channel(event_type: str, channel: str) -> bool:
    channel = (channel or "").strip().lower()
    if not channel or channel == "all":
        return True

    et = (event_type or "").lower().strip()

    if channel == "email":
        return not any(et.startswith(prefix) for prefix in ("sms_", "linkedin_", "call_"))

    return et.startswith(f"{channel}_") or et == channel


def _metrics_from_events(events: List[Dict[str, Any]]) -> Dict[str, int]:
    emails_sent = 0
    opens = 0
    clicks = 0
    replies = 0
    conversions = 0

    for e in events:
        et = _normalize_event_type(e.get("event_type"))

        if et == "sent":
            emails_sent += 1
        elif et == "open":
            opens += 1
        elif et == "click":
            clicks += 1
        elif et == "reply":
            replies += 1
        elif et == "convert":
            conversions += 1

    return {
        "emails_sent": emails_sent,
        "opens": opens,
        "clicks": clicks,
        "replies": replies,
        "conversions": conversions,
    }


def _rates(m: Dict[str, int]) -> Dict[str, float]:
    sent = m["emails_sent"]

    return {
        **m,
        "open_rate": (m["opens"] / sent) if sent else 0,
        "click_rate": (m["clicks"] / sent) if sent else 0,
        "reply_rate": (m["replies"] / sent) if sent else 0,
        "conversion_rate": (m["conversions"] / sent) if sent else 0,
    }


def _funnel(m: Dict[str, int]) -> Dict[str, float]:
    sent = m["emails_sent"]
    rep = m["replies"]
    conv = m["conversions"]

    return {
        "total_sent": sent,
        "replied": rep,
        "converted": conv,
        "drop_off_to_reply_pct": ((sent - rep) / sent * 100) if sent else 0,
        "drop_off_to_conversion_pct": ((rep - conv) / rep * 100) if rep else 0,
    }


def _recommend(m: Dict[str, Any]) -> List[str]:
    r = []
    if m["opens"] < 1:
        r.append("Improve deliverability / subject lines")
    if m["reply_rate"] < 0.1:
        r.append("Improve CTA / personalization")
    if m["click_rate"] < 0.05:
        r.append("Add a clearer CTA or stronger offer")
    return r


def get_campaign_dashboard(campaign_id: int, channel: str = "") -> Dict[str, Any]:
    name = _get_campaign_name(campaign_id)

    events = get_campaign_events(campaign_id) or []
    filtered_events = [
        e for e in events
        if _event_matches_channel(e.get("event_type"), channel)
    ]

    base = _metrics_from_events(filtered_events)
    m = _rates(base)
    step_funnel = followup_effectiveness(campaign_id)

    return {
        "campaign_id": campaign_id,
        "campaign_name": name,
        "channel": channel or "all",

        "emails_sent": m["emails_sent"],
        "opens": m["opens"],
        "clicks": m["clicks"],
        "replies": m["replies"],
        "conversions": m["conversions"],

        "open_rate": m["open_rate"],
        "click_rate": m["click_rate"],
        "reply_rate": m["reply_rate"],
        "conversion_rate": m["conversion_rate"],

        "funnel": _funnel(m),
        "followup_steps": step_funnel,
        "recommendations": _recommend(m),

        "total_events": len(filtered_events),
    }


def get_all_campaigns_dashboard(channel: str = "") -> List[Dict[str, Any]]:
    try:
        res = supabase.table("campaigns").select("id").execute()
        return [
            get_campaign_dashboard(c["id"], channel=channel)
            for c in (res.data or [])
        ]
    except Exception:
        return []