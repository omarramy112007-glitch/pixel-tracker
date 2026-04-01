# analytics/crm_analytics.py

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase

TABLE_NAME = "crm_analytics"


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_timestamp(value: Optional[datetime]) -> str:
    if value is None:
        return datetime.utcnow().isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def update_crm_metrics(
    lead_id: int,
    emails_sent: int = 0,
    opens: int = 0,
    clicks: int = 0,
    replies: int = 0,
    conversions: int = 0,
    last_activity: datetime | str | None = None,
) -> None:
    """
    Updates or inserts CRM analytics metrics for a lead.
    """
    if lead_id is None:
        print("⚠️ update_crm_metrics skipped: lead_id is required")
        return

    last_activity_value = _normalize_timestamp(last_activity if isinstance(last_activity, datetime) else None)
    if isinstance(last_activity, str):
        last_activity_value = last_activity

    existing = (
        supabase.table(TABLE_NAME)
        .select("*")
        .eq("lead_id", lead_id)
        .execute()
    )

    if existing.data and len(existing.data) > 0:
        row = existing.data[0]

        payload = {
            "emails_sent": _safe_int(row.get("emails_sent")) + _safe_int(emails_sent),
            "opens": _safe_int(row.get("opens")) + _safe_int(opens),
            "clicks": _safe_int(row.get("clicks")) + _safe_int(clicks),
            "replies": _safe_int(row.get("replies")) + _safe_int(replies),
            "conversions": _safe_int(row.get("conversions")) + _safe_int(conversions),
            "last_activity": last_activity_value,
        }

        supabase.table(TABLE_NAME).update(payload).eq("lead_id", lead_id).execute()

    else:
        payload = {
            "lead_id": lead_id,
            "engagement_score": 0,
            "emails_sent": _safe_int(emails_sent),
            "opens": _safe_int(opens),
            "clicks": _safe_int(clicks),
            "replies": _safe_int(replies),
            "conversions": _safe_int(conversions),
            "last_activity": last_activity_value,
        }

        supabase.table(TABLE_NAME).insert(payload).execute()


def compute_engagement_score(metrics: dict) -> float:
    """
    Example scoring: weighted sum
    Emails sent: 1pt
    Opens: 2pt
    Clicks: 3pt
    Replies: 5pt
    Conversions: 10pt
    """
    metrics = metrics or {}

    score = (
        _safe_int(metrics.get("emails_sent")) * 1 +
        _safe_int(metrics.get("opens")) * 2 +
        _safe_int(metrics.get("clicks")) * 3 +
        _safe_int(metrics.get("replies")) * 5 +
        _safe_int(metrics.get("conversions")) * 10
    )
    return float(score)