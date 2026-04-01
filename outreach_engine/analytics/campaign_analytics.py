# outreach_engine/analytics/campaign_analytics.py

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase
from outreach_engine.core.lead_manager import get_campaign_leads, get_lead

LEAD_TABLE = "outreach_leads"
CRM_TABLE = "crm_analytics"


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _best_effort_update_crm(lead_id: Optional[int], updates: Dict[str, Any]) -> None:
    """
    Best-effort update for crm_analytics.
    This must never break the send flow.
    """
    if not lead_id:
        return

    try:
        existing = (
            supabase.table(CRM_TABLE)
            .select("*")
            .eq("lead_id", lead_id)
            .execute()
        )

        now = _now_iso()

        if existing.data and len(existing.data) > 0:
            row = existing.data[0]
            payload = {
                "lead_id": lead_id,
                "last_activity": now,
            }

            for key, value in updates.items():
                if key in {"emails_sent", "opens", "clicks", "replies", "conversions"}:
                    payload[key] = int(row.get(key, 0) or 0) + int(value or 0)
                elif key == "engagement_score":
                    payload[key] = value
                else:
                    payload[key] = value

            supabase.table(CRM_TABLE).update(payload).eq("lead_id", lead_id).execute()
        else:
            payload = {
                "lead_id": lead_id,
                "engagement_score": updates.get("engagement_score", 0) or 0,
                "emails_sent": int(updates.get("emails_sent", 0) or 0),
                "opens": int(updates.get("opens", 0) or 0),
                "clicks": int(updates.get("clicks", 0) or 0),
                "replies": int(updates.get("replies", 0) or 0),
                "conversions": int(updates.get("conversions", 0) or 0),
                "last_activity": now,
            }
            supabase.table(CRM_TABLE).insert(payload).execute()

    except Exception as e:
        print(f"⚠️ crm_analytics update skipped: {e}")


def _update_outreach_lead(lead_id: Optional[int], updates: Dict[str, Any]) -> None:
    if not lead_id:
        return

    try:
        updates = dict(updates)
        updates["last_updated"] = _now_iso()

        supabase.table(LEAD_TABLE).update(updates).eq("id", lead_id).execute()
    except Exception as e:
        print(f"⚠️ outreach lead update failed: {e}")


def _increment_outreach_counter(
    lead_id: Optional[int],
    counter_field: str,
    extra_updates: Optional[Dict[str, Any]] = None,
) -> None:
    if not lead_id:
        return

    try:
        resp = (
            supabase.table(LEAD_TABLE)
            .select(counter_field)
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        current = 0
        if resp.data:
            current = int(resp.data[0].get(counter_field, 0) or 0)

        updates = {
            counter_field: current + 1,
            "last_updated": _now_iso(),
        }

        if extra_updates:
            updates.update(extra_updates)

        supabase.table(LEAD_TABLE).update(updates).eq("id", lead_id).execute()

    except Exception as e:
        print(f"⚠️ counter update failed ({counter_field}): {e}")


# --------------------------------------------------
# Event Recording
# --------------------------------------------------
def record_email_sent(
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Record a sent email and update outreach lead status.
    """
    if lead_id:
        _update_outreach_lead(
            lead_id,
            {
                "status": "sent",
                "last_email_sent": _now_iso(),
            },
        )
        _best_effort_update_crm(
            lead_id,
            {
                "emails_sent": 1,
            },
        )


def record_email_provider(
    campaign_id: int,
    provider: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Provider tracking is optional in the current schema.
    This is kept as a safe no-op so it never breaks sending.
    """
    try:
        print(f"📦 Email provider used for campaign {campaign_id}: {provider}")
    except Exception as e:
        print(f"⚠️ record_email_provider skipped: {e}")


def record_open(
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if lead_id:
        _increment_outreach_counter(
            lead_id,
            "open_count",
            {
                "email_opened": True,
                "email_opened_at": _now_iso(),
            },
        )
        _best_effort_update_crm(
            lead_id,
            {
                "opens": 1,
            },
        )


def record_click(
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if lead_id:
        _increment_outreach_counter(
            lead_id,
            "click_count",
            {
                "link_clicked": True,
                "link_clicked_at": _now_iso(),
            },
        )
        _best_effort_update_crm(
            lead_id,
            {
                "clicks": 1,
            },
        )


def record_reply(
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if lead_id:
        _increment_outreach_counter(
            lead_id,
            "reply_count",
            {
                "status": "replied",
                "replied_at": _now_iso(),
            },
        )
        _best_effort_update_crm(
            lead_id,
            {
                "replies": 1,
            },
        )


def record_conversion(
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if lead_id:
        _increment_outreach_counter(
            lead_id,
            "conversion_count",
            {
                "status": "converted",
                "converted_at": _now_iso(),
            },
        )
        _best_effort_update_crm(
            lead_id,
            {
                "conversions": 1,
            },
        )


# --------------------------------------------------
# Real-time Metrics
# --------------------------------------------------
def get_real_time_metrics(campaign_id: int) -> Dict[str, Any]:
    leads = get_campaign_leads(campaign_id) or []

    metrics = {
        "emails_sent": sum(1 for l in leads if l.get("status") in ["sent", "replied", "converted"]),
        "opens": sum(1 for l in leads if l.get("email_opened") or (l.get("open_count") or 0) > 0),
        "clicks": sum(1 for l in leads if l.get("link_clicked") or (l.get("click_count") or 0) > 0),
        "replies": sum(1 for l in leads if l.get("status") == "replied" or (l.get("reply_count") or 0) > 0),
        "conversions": sum(1 for l in leads if l.get("status") == "converted" or (l.get("conversion_count") or 0) > 0),
    }

    metrics["open_rate"] = round((metrics["opens"] / metrics["emails_sent"] * 100), 1) if metrics["emails_sent"] else 0
    metrics["click_through_rate"] = round((metrics["clicks"] / metrics["emails_sent"] * 100), 1) if metrics["emails_sent"] else 0
    metrics["reply_rate"] = round((metrics["replies"] / metrics["emails_sent"] * 100), 1) if metrics["emails_sent"] else 0
    metrics["conversion_rate"] = round((metrics["conversions"] / metrics["emails_sent"] * 100), 1) if metrics["emails_sent"] else 0

    return metrics


# --------------------------------------------------
# Funnel Analysis
# --------------------------------------------------
def get_campaign_funnel(campaign_id: int) -> Dict[str, Any]:
    leads = get_campaign_leads(campaign_id) or []

    total_sent = sum(1 for l in leads if l.get("status") in ["sent", "replied", "converted"])
    replied = sum(1 for l in leads if l.get("status") == "replied" or (l.get("reply_count") or 0) > 0)
    converted = sum(1 for l in leads if l.get("status") == "converted" or (l.get("conversion_count") or 0) > 0)

    drop_off_reply = ((total_sent - replied) / total_sent * 100) if total_sent else 0
    drop_off_conversion = ((replied - converted) / replied * 100) if replied else 0

    return {
        "total_sent": total_sent,
        "replied": replied,
        "converted": converted,
        "drop_off_to_reply_pct": round(drop_off_reply, 1),
        "drop_off_to_conversion_pct": round(drop_off_conversion, 1),
    }


# --------------------------------------------------
# Lead & Campaign Engagement
# --------------------------------------------------
def get_lead_engagement_rate(lead: Dict[str, Any]) -> float:
    score = lead.get("engagement_score", 0) or 0
    return min(float(score) / 10, 1.0)


def get_campaign_engagement(campaign_id: int) -> float:
    leads = get_campaign_leads(campaign_id) or []
    rates = [get_lead_engagement_rate(l) for l in leads]
    return sum(rates) / len(rates) if rates else 0


# --------------------------------------------------
# ROI / Deals vs Emails
# --------------------------------------------------
def calculate_campaign_roi(campaign_id: int) -> float:
    metrics = get_real_time_metrics(campaign_id)
    emails_sent = metrics.get("emails_sent", 1) or 1
    conversions = metrics.get("conversions", 0) or 0
    return conversions / emails_sent


# --------------------------------------------------
# Follow-up Effectiveness
# --------------------------------------------------
def followup_effectiveness(campaign_id: int) -> Dict[Any, Dict[str, Any]]:
    from outreach_engine.database.event_repository import get_events

    events = get_events(campaign_id) or []
    steps: Dict[Any, Dict[str, Any]] = {}

    for e in events:
        step = (e.get("metadata") or {}).get("step", 0)
        if step not in steps:
            steps[step] = {"sent": 0, "replied": 0}

        if e.get("event_type") == "sent":
            steps[step]["sent"] += 1

        if e.get("event_type") == "replied":
            steps[step]["replied"] += 1

    for step in steps:
        s = steps[step]["sent"]
        r = steps[step]["replied"]
        steps[step]["conversion"] = r / s if s else 0

    return steps


# --------------------------------------------------
# Alerts
# --------------------------------------------------
def check_delivery_alert(campaign_id: int, threshold: float = 0.8) -> bool:
    metrics = get_real_time_metrics(campaign_id)
    sent = metrics.get("emails_sent", 0) or 0
    delivered = metrics.get("opens", 0) or 0
    delivery_rate = delivered / sent if sent else 1.0

    if delivery_rate < threshold:
        print(f"⚠ Delivery rate low: {delivery_rate * 100:.1f}% for campaign {campaign_id}")
        return True

    return False