# File: outreach_engine/analytics/crm_analytics.py

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


def compute_engagement_score(metrics: dict) -> float:
    metrics = metrics or {}
    return float(
        _safe_int(metrics.get("emails_sent")) * 1
        + _safe_int(metrics.get("opens")) * 2
        + _safe_int(metrics.get("clicks")) * 3
        + _safe_int(metrics.get("replies")) * 5
        + _safe_int(metrics.get("conversions")) * 10
    )


def update_crm_metrics(
    lead_id: int,
    emails_sent: int = 0,
    opens: int = 0,
    clicks: int = 0,
    replies: int = 0,
    conversions: int = 0,
    last_activity: datetime | str | None = None,
    campaign_id: int | None = None,
    emails_per_provider: Optional[Dict[str, int]] = None,
    replace: bool = False,
) -> Dict[str, Any]:
    """
    Writes lead-level CRM analytics into Supabase.
    This is called by event_router after each event.
    """

    if lead_id is None:
        return {"error": "lead_id required"}

    try:
        existing = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("lead_id", lead_id)
            .limit(1)
            .execute()
        )

        row = existing.data[0] if existing.data else None

        if replace or not row:
            new_metrics = {
                "lead_id": lead_id,
                "emails_sent": _safe_int(emails_sent),
                "opens": _safe_int(opens),
                "clicks": _safe_int(clicks),
                "replies": _safe_int(replies),
                "conversions": _safe_int(conversions),
                "last_activity": (
                    last_activity.isoformat()
                    if isinstance(last_activity, datetime)
                    else last_activity
                ) or datetime.utcnow().isoformat(),
            }

            payload = {
                **new_metrics,
                "engagement_score": compute_engagement_score(new_metrics),
            }

            result = supabase.table(TABLE_NAME).upsert(payload).execute()
            return {"status": "success", "data": result.data}

        updated = {
            "emails_sent": _safe_int(row.get("emails_sent")) + _safe_int(emails_sent),
            "opens": _safe_int(row.get("opens")) + _safe_int(opens),
            "clicks": _safe_int(row.get("clicks")) + _safe_int(clicks),
            "replies": _safe_int(row.get("replies")) + _safe_int(replies),
            "conversions": _safe_int(row.get("conversions")) + _safe_int(conversions),
            "last_activity": (
                last_activity.isoformat()
                if isinstance(last_activity, datetime)
                else last_activity
            ) or datetime.utcnow().isoformat(),
        }

        updated["engagement_score"] = compute_engagement_score(updated)

        result = (
            supabase.table(TABLE_NAME)
            .update(updated)
            .eq("lead_id", lead_id)
            .execute()
        )

        return {"status": "success", "data": result.data}

    except Exception as e:
        return {"status": "error", "message": str(e)}