# outreach_engine/analytics/dashboard_data.py

from __future__ import annotations

from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime, timedelta, timezone

from outreach_engine.database.supabase_client import supabase

CHANNEL_PREFIXES = {"email", "sms", "linkedin", "call"}

OPEN_EVENTS = {"open", "opened", "pixel_open", "email_opened"}
CLICK_EVENTS = {"click", "clicked", "link_clicked"}
REPLY_EVENTS = {"reply", "replied", "email_replied"}
SENT_EVENTS = {"sent", "email_sent", "send"}
CONVERT_EVENTS = {"convert", "converted", "conversion", "deal_closed"}


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _normalize_campaign_id(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _get_week_filter():
    return datetime.now(timezone.utc) - timedelta(days=7)


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


def _split_channel_event(raw_event_type: str) -> Tuple[str, str]:
    et = (raw_event_type or "").lower().strip()
    channel = "email"

    if "_" in et:
        prefix, suffix = et.split("_", 1)
        if prefix in CHANNEL_PREFIXES:
            channel = prefix
            et = suffix

    if et in OPEN_EVENTS:
        return channel, "open"
    if et in CLICK_EVENTS:
        return channel, "click"
    if et in REPLY_EVENTS:
        return channel, "reply"
    if et in SENT_EVENTS:
        return channel, "sent"
    if et in CONVERT_EVENTS:
        return channel, "convert"

    return channel, et


def _event_matches_channel(event_type: str, channel: str) -> bool:
    channel = (channel or "").strip().lower()
    if not channel or channel == "all":
        return True

    event_channel, _ = _split_channel_event(event_type)
    return event_channel == channel


def _is_positive_reply_status(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    value = str(value).strip().lower()
    return value in {"replied", "reply", "true", "1", "yes", "won"}


def _get_campaign_events(campaign_id: int, include_last_7_days: bool = False) -> List[Dict[str, Any]]:
    week_filter = _get_week_filter()

    def _apply_week(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not include_last_7_days:
            return rows
        filtered = []
        for r in rows:
            ts = r.get("created_at") or r.get("timestamp") or r.get("time")
            try:
                if ts and datetime.fromisoformat(str(ts).replace("Z", "+00:00")) >= week_filter:
                    filtered.append(r)
            except Exception:
                continue
        return filtered

    try:
        res = (
            supabase.table("lead_events")
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
        )
        direct = _apply_week(res.data or [])
        if direct:
            return direct
    except Exception:
        pass

    try:
        res = supabase.table("lead_events").select("*").execute()
        all_events = res.data or []
        filtered = []
        for e in all_events:
            if _normalize_campaign_id(e.get("campaign_id")) == campaign_id:
                filtered.append(e)
        return _apply_week(filtered)
    except Exception:
        return []


def _events_metrics(events: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {
        "emails_sent": 0,
        "sms_sent": 0,
        "linkedin_sent": 0,
        "calls_made": 0,
        "opens": 0,
        "clicks": 0,
        "replies": 0,
        "conversions": 0,
    }

    for e in events:
        channel, et = _split_channel_event(e.get("event_type"))

        if et == "sent":
            if channel == "sms":
                counts["sms_sent"] += 1
            elif channel == "linkedin":
                counts["linkedin_sent"] += 1
            elif channel == "call":
                counts["calls_made"] += 1
            else:
                counts["emails_sent"] += 1

        elif et == "open":
            counts["opens"] += 1
        elif et == "click":
            counts["clicks"] += 1
        elif et == "reply":
            counts["replies"] += 1
        elif et == "convert":
            counts["conversions"] += 1

    return counts


def _lead_table_metrics(campaign_id: int, include_last_7_days: bool = False) -> Dict[str, int]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select(
                "status, open_count, click_count, reply_count, conversion_count, "
                "reply_status, last_email_sent, replied_at, email_opened, "
                "email_opened_at, deal_status, deal_closed, created_at"
            )
            .eq("campaign_id", campaign_id)
            .execute()
        )
    except Exception:
        return {
            "total_leads": 0,
            "emails_sent": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
        }

    rows = res.data or []

    if include_last_7_days:
        week_filter = _get_week_filter()
        tmp = []
        for r in rows:
            try:
                if r.get("created_at") and datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00")) >= week_filter:
                    tmp.append(r)
            except Exception:
                continue
        rows = tmp

    sent = sum(
        1
        for r in rows
        if r.get("last_email_sent")
        or r.get("replied_at")
        or str(r.get("status") or "").lower() in {"sent", "replied", "converted"}
    )

    opens = sum(_safe_int(r.get("open_count")) for r in rows)
    opens += sum(1 for r in rows if bool(r.get("email_opened")))

    clicks = sum(_safe_int(r.get("click_count")) for r in rows)

    replies = sum(_safe_int(r.get("reply_count")) for r in rows)
    replies += sum(1 for r in rows if _is_positive_reply_status(r.get("reply_status")))
    replies += sum(1 for r in rows if str(r.get("status") or "").lower() == "replied")

    conversions = sum(_safe_int(r.get("conversion_count")) for r in rows)
    conversions += sum(
        1
        for r in rows
        if str(r.get("deal_status") or "").lower() in {"won", "converted"} or bool(r.get("deal_closed"))
    )

    return {
        "total_leads": len(rows),
        "emails_sent": sent,
        "opens": opens,
        "clicks": clicks,
        "replies": replies,
        "conversions": conversions,
    }


def _merge_metrics(events_m: Dict[str, int], lead_m: Dict[str, int]) -> Dict[str, int]:
    merged = {}
    for key in ["emails_sent", "opens", "clicks", "replies", "conversions"]:
        merged[key] = max(_safe_int(events_m.get(key, 0)), _safe_int(lead_m.get(key, 0)))

    merged["total_leads"] = _safe_int(lead_m.get("total_leads", 0))
    return merged


def _rates(m: Dict[str, int]) -> Dict[str, float]:
    sent = m["emails_sent"]
    return {
        **m,
        "open_rate": round((m["opens"] / sent) * 100, 1) if sent else 0.0,
        "click_rate": round((m["clicks"] / sent) * 100, 1) if sent else 0.0,
        "reply_rate": round((m["replies"] / sent) * 100, 1) if sent else 0.0,
        "conversion_rate": round((m["conversions"] / sent) * 100, 1) if sent else 0.0,
    }


def _funnel(m: Dict[str, int]) -> Dict[str, float]:
    sent = m["emails_sent"]
    rep = m["replies"]
    conv = m["conversions"]

    return {
        "total_sent": sent,
        "replied": rep,
        "converted": conv,
        "drop_off_to_reply_pct": round(((sent - rep) / sent) * 100, 1) if sent else 0.0,
        "drop_off_to_conversion_pct": round(((rep - conv) / rep) * 100, 1) if rep else 0.0,
    }


def _recommend(m: Dict[str, Any]) -> List[str]:
    recommendations = []
    if m["emails_sent"] and m["opens"] < 1:
        recommendations.append("Improve deliverability / subject lines")
    if m["reply_rate"] < 10:
        recommendations.append("Improve CTA / personalization")
    if m["click_rate"] < 5:
        recommendations.append("Add a clearer CTA or stronger offer")
    return recommendations


def _build_dashboard_payload(
    campaign_id: int,
    channel: str = "",
    include_last_7_days: bool = False,
) -> Dict[str, Any]:
    name = _get_campaign_name(campaign_id)
    channel = (channel or "").strip().lower()

    events = _get_campaign_events(campaign_id, include_last_7_days=include_last_7_days)

    filtered_events = [
        e for e in events
        if _event_matches_channel(e.get("event_type"), channel)
    ]

    event_metrics = _events_metrics(filtered_events)
    lead_metrics = _lead_table_metrics(campaign_id, include_last_7_days=include_last_7_days)

    if channel in {"sms", "linkedin", "call"}:
        merged = {
            "emails_sent": event_metrics["emails_sent"],
            "sms_sent": event_metrics["sms_sent"],
            "linkedin_sent": event_metrics["linkedin_sent"],
            "calls_made": event_metrics["calls_made"],
            "opens": event_metrics["opens"],
            "clicks": event_metrics["clicks"],
            "replies": event_metrics["replies"],
            "conversions": event_metrics["conversions"],
            "total_leads": lead_metrics.get("total_leads", 0),
        }
    else:
        merged = _merge_metrics(event_metrics, lead_metrics)
        merged["sms_sent"] = event_metrics["sms_sent"]
        merged["linkedin_sent"] = event_metrics["linkedin_sent"]
        merged["calls_made"] = event_metrics["calls_made"]

    m = _rates(merged)

    followup_steps: Dict[int, Dict[str, int]] = {}
    for e in filtered_events:
        step = (e.get("metadata") or {}).get("step", 0)
        try:
            step = int(step)
        except Exception:
            step = 0

        followup_steps.setdefault(step, {"sent": 0, "replied": 0})
        _, et = _split_channel_event(e.get("event_type"))
        if et == "sent":
            followup_steps[step]["sent"] += 1
        if et == "reply":
            followup_steps[step]["replied"] += 1

    return {
        "campaign_id": campaign_id,
        "campaign_name": name,
        "channel": channel or "all",
        "total_leads": m.get("total_leads", 0),
        "emails_sent": m["emails_sent"],
        "sms_sent": m.get("sms_sent", 0),
        "linkedin_sent": m.get("linkedin_sent", 0),
        "calls_made": m.get("calls_made", 0),
        "opens": m["opens"],
        "clicks": m["clicks"],
        "replies": m["replies"],
        "conversions": m["conversions"],
        "open_rate": m["open_rate"],
        "click_rate": m["click_rate"],
        "reply_rate": m["reply_rate"],
        "conversion_rate": m["conversion_rate"],
        "funnel": _funnel(m),
        "followup_steps": followup_steps,
        "recommendations": _recommend(m),
        "total_events": len(filtered_events),
        "metrics": m,
        "last_7_days_mode": include_last_7_days,
        "total_expected_revenue": 0,
        "avg_expected_revenue": 0,
    }


def get_campaign_dashboard(
    campaign_id: int,
    channel: str = "",
    include_last_7_days: bool = False,
) -> Dict[str, Any]:
    return _build_dashboard_payload(campaign_id, channel, include_last_7_days)


def get_all_campaigns_dashboard(
    channel: str = "",
    include_last_7_days: bool = False,
) -> List[Dict[str, Any]]:
    try:
        res = supabase.table("campaigns").select("id").execute()
        return [
            _build_dashboard_payload(
                c["id"],
                channel=channel,
                include_last_7_days=include_last_7_days,
            )
            for c in (res.data or [])
            if c.get("id") is not None
        ]
    except Exception:
        return []