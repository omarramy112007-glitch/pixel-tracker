from datetime import datetime, date
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase
from outreach_engine.core.lead_manager import get_campaign_leads

TABLE_NAME = "crm_analytics"
LEAD_TABLE = "outreach_leads"


def _update_metric(campaign_id: int, column: str, increment: int = 1) -> None:
    """
    Best-effort campaign metric update.
    If the campaign_analytics table does not exist, this safely no-ops
    instead of crashing the send flow.
    """
    try:
        today = str(date.today())

        existing = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("campaign_id", campaign_id)
            .eq("created_at", today)
            .execute()
        )

        if existing.data and len(existing.data) > 0:
            row_id = existing.data[0]["id"]
            current_value = existing.data[0].get(column, 0) or 0

            supabase.table(TABLE_NAME).update({
                column: current_value + increment
            }).eq("id", row_id).execute()
        else:
            payload = {
                "campaign_id": campaign_id,
                "emails_sent": 0,
                "opens": 0,
                "clicks": 0,
                "replies": 0,
                "conversions": 0,
                "emails_per_provider": {},
                "created_at": today,
                column: increment
            }

            supabase.table(TABLE_NAME).insert(payload).execute()

    except Exception as e:
        print(f"⚠️ campaign analytics update skipped: {e}")


def _update_outreach_lead(lead_id: Optional[int], updates: Dict[str, Any]) -> None:
    if not lead_id:
        return

    try:
        supabase.table(LEAD_TABLE).update(updates).eq("id", lead_id).execute()
    except Exception as e:
        print(f"⚠️ outreach lead update failed: {e}")


def _increment_outreach_counter(
    lead_id: Optional[int],
    counter_field: str,
    extra_updates: Optional[Dict[str, Any]] = None
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
            current = resp.data[0].get(counter_field, 0) or 0

        updates = {
            counter_field: current + 1,
            "last_updated": datetime.utcnow().isoformat(),
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
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Record a sent email and update outreach lead status.
    metadata is accepted for backward compatibility and ignored safely.
    """
    _update_metric(campaign_id, "emails_sent")

    if lead_id:
        _update_outreach_lead(lead_id, {
            "status": "sent",
            "last_email_sent": datetime.utcnow().isoformat(),
            "last_updated": datetime.utcnow().isoformat(),
        })


def record_email_provider(
    campaign_id: int,
    provider: str,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Track emails sent per provider.
    metadata is accepted for backward compatibility and ignored safely.
    """
    try:
        today = str(date.today())

        existing = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("campaign_id", campaign_id)
            .eq("created_at", today)
            .execute()
        )

        if existing.data and len(existing.data) > 0:
            row = existing.data[0]
            providers = row.get("emails_per_provider", {}) or {}
            providers[provider] = providers.get(provider, 0) + 1

            supabase.table(TABLE_NAME).update({
                "emails_per_provider": providers
            }).eq("id", row["id"]).execute()
        else:
            supabase.table(TABLE_NAME).insert({
                "campaign_id": campaign_id,
                "emails_sent": 0,
                "opens": 0,
                "clicks": 0,
                "replies": 0,
                "conversions": 0,
                "emails_per_provider": {provider: 1},
                "created_at": today
            }).execute()

    except Exception as e:
        print(f"⚠️ record_email_provider skipped: {e}")


def record_open(
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    _update_metric(campaign_id, "opens")
    _increment_outreach_counter(
        lead_id,
        "open_count",
        {
            "email_opened": True,
            "email_opened_at": datetime.utcnow().isoformat(),
        }
    )


def record_click(
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    _update_metric(campaign_id, "clicks")
    _increment_outreach_counter(
        lead_id,
        "click_count",
        {
            "link_clicked": True,
            "link_clicked_at": datetime.utcnow().isoformat(),
        }
    )


def record_reply(
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    _update_metric(campaign_id, "replies")
    _increment_outreach_counter(
        lead_id,
        "reply_count",
        {
            "status": "replied",
            "replied_at": datetime.utcnow().isoformat(),
        }
    )


def record_conversion(
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    _update_metric(campaign_id, "conversions")
    _increment_outreach_counter(
        lead_id,
        "conversion_count",
        {
            "status": "converted",
            "converted_at": datetime.utcnow().isoformat(),
        }
    )


# --------------------------------------------------
# Real-time Metrics
# --------------------------------------------------
def get_real_time_metrics(campaign_id: int) -> Dict[str, Any]:
    leads = get_campaign_leads(campaign_id) or []

    metrics = {
        "emails_sent": sum(1 for l in leads if l.get("status") in ["sent", "replied", "converted"]),
        "opens": sum(1 for l in leads if l.get("email_opened")),
        "clicks": sum(1 for l in leads if l.get("link_clicked")),
        "replies": sum(1 for l in leads if l.get("status") == "replied"),
        "conversions": sum(1 for l in leads if l.get("status") == "converted")
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
    replied = sum(1 for l in leads if l.get("status") == "replied")
    converted = sum(1 for l in leads if l.get("status") == "converted")

    drop_off_reply = ((total_sent - replied) / total_sent * 100) if total_sent else 0
    drop_off_conversion = ((replied - converted) / replied * 100) if replied else 0

    return {
        "total_sent": total_sent,
        "replied": replied,
        "converted": converted,
        "drop_off_to_reply_pct": round(drop_off_reply, 1),
        "drop_off_to_conversion_pct": round(drop_off_conversion, 1)
    }


# --------------------------------------------------
# Lead & Campaign Engagement
# --------------------------------------------------
def get_lead_engagement_rate(lead: Dict[str, Any]) -> float:
    score = lead.get("engagement_score", 0) or 0
    return min(score / 10, 1.0)


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