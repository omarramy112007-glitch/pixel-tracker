# File: outreach_engine/analytics/crm_analytics.py

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase

TABLE_NAME = "crm_analytics"

WEEKLY_WINDOW_DAYS = 7


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_iso(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value
    return datetime.utcnow().isoformat()


def _is_outdated(last_activity: Any) -> bool:
    try:
        if not last_activity:
            return True
        if isinstance(last_activity, str):
            last_activity_dt = datetime.fromisoformat(last_activity.replace("Z", ""))
        elif isinstance(last_activity, datetime):
            last_activity_dt = last_activity
        else:
            return True

        return datetime.utcnow() - last_activity_dt > timedelta(days=WEEKLY_WINDOW_DAYS)
    except Exception:
        return True


def compute_engagement_score(metrics: dict | None) -> float:
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

    if lead_id is None:
        return {"status": "error", "message": "lead_id required"}

    try:
        existing = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("lead_id", lead_id)
            .limit(1)
            .execute()
        )

        row = existing.data[0] if existing.data else None
        activity_ts = _to_iso(last_activity)

        force_reset = False
        if row:
            force_reset = _is_outdated(row.get("last_activity"))

        if replace or not row or force_reset:
            payload = {
                "lead_id": lead_id,
                "emails_sent": _safe_int(emails_sent),
                "opens": _safe_int(opens),
                "clicks": _safe_int(clicks),
                "replies": _safe_int(replies),
                "conversions": _safe_int(conversions),
                "last_activity": activity_ts,
                "engagement_score": compute_engagement_score(
                    {
                        "emails_sent": emails_sent,
                        "opens": opens,
                        "clicks": clicks,
                        "replies": replies,
                        "conversions": conversions,
                    }
                ),
            }

            result = supabase.table(TABLE_NAME).upsert(payload).execute()
            return {"status": "success", "data": result.data}

        updated = {
            "emails_sent": _safe_int(row.get("emails_sent")) + _safe_int(emails_sent),
            "opens": _safe_int(row.get("opens")) + _safe_int(opens),
            "clicks": _safe_int(row.get("clicks")) + _safe_int(clicks),
            "replies": _safe_int(row.get("replies")) + _safe_int(replies),
            "conversions": _safe_int(row.get("conversions")) + _safe_int(conversions),
            "last_activity": activity_ts,
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