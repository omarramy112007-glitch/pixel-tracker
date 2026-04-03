# outreach_engine/core/system_monitor.py

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from outreach_engine.database.supabase_client import supabase

CAMPAIGN_TABLE = "campaigns"
ANALYTICS_TABLE = "crm_analytics"
FAILURE_TABLE = "system_failures"
PERF_TABLE = "system_performance"
OUTREACH_LEADS_TABLE = "outreach_leads"


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _today_start() -> datetime:
    now = datetime.utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def get_system_health() -> dict:
    """
    Returns overall system health metrics.
    """
    now = datetime.utcnow()
    today_start = _today_start()

    # crm analytics rows updated today
    analytics_res = (
        supabase.table(ANALYTICS_TABLE)
        .select("lead_id, emails_sent, last_activity")
        .execute()
    )
    analytics_rows = analytics_res.data or []

    emails_sent_today = 0
    for row in analytics_rows:
        last_activity = _parse_iso(row.get("last_activity"))
        if last_activity and last_activity >= today_start:
            emails_sent_today += int(row.get("emails_sent", 0) or 0)

    # Active campaigns
    campaigns_res = (
        supabase.table(CAMPAIGN_TABLE)
        .select("id")
        .eq("active", True)
        .execute()
    )
    active_campaigns = len(campaigns_res.data or [])

    # Queue size from outreach_leads
    leads_res = (
        supabase.table(OUTREACH_LEADS_TABLE)
        .select("id, status, next_followup")
        .execute()
    )
    leads_rows = leads_res.data or []
    pending_statuses = {"pending", "new", "not_contacted"}
    queue_size = sum(
        1 for row in leads_rows
        if (row.get("status") or "").lower().strip() in pending_statuses
    )

    # Failed emails / failures
    failures_res = supabase.table(FAILURE_TABLE).select("id").execute()
    failed_emails = len(failures_res.data or [])

    perf_metrics = get_performance_metrics()
    avg_send_time = perf_metrics.get("avg_send_time", 0)

    return {
        "emails_sent_today": emails_sent_today,
        "active_campaigns": active_campaigns,
        "queue_size": queue_size,
        "failed_emails": failed_emails,
        "avg_send_time": avg_send_time,
        "timestamp": now.isoformat()
    }


def get_campaign_status() -> list:
    """
    Returns list of all campaigns with id, name, active status.
    """
    res = (
        supabase.table(CAMPAIGN_TABLE)
        .select("id, name, active, daily_limit, created_at")
        .execute()
    )

    campaigns = []
    for row in (res.data or []):
        campaigns.append({
            "id": row.get("id"),
            "name": row.get("name"),
            "active": bool(row.get("active", False)),
            "status": "active" if row.get("active") else "paused",
            "daily_limit": row.get("daily_limit", 0),
            "created_at": row.get("created_at"),
        })

    return campaigns


def get_queue_status() -> dict:
    """
    Returns queue size and status based on outreach_leads.
    """
    res = (
        supabase.table(OUTREACH_LEADS_TABLE)
        .select("id, status, next_followup")
        .execute()
    )
    rows = res.data or []

    pending_statuses = {"pending", "new", "not_contacted"}
    queue_size = sum(
        1 for row in rows
        if (row.get("status") or "").lower().strip() in pending_statuses
    )

    return {
        "queue_size": queue_size,
        "status": "healthy" if queue_size < 1000 else "high_load"
    }


def get_performance_metrics() -> dict:
    """
    Returns avg send time, emails/sec, queue latency, API response time.
    """
    now = datetime.utcnow()

    # Avg execution time
    exec_res = supabase.table(PERF_TABLE).select("execution_time, function_name").execute()
    exec_rows = exec_res.data or []
    exec_times = [
        row["execution_time"]
        for row in exec_rows
        if row.get("execution_time") is not None
    ]
    avg_send_time = sum(exec_times) / len(exec_times) if exec_times else 0

    # Emails per second from crm_analytics last_activity + emails_sent
    emails_res = supabase.table(ANALYTICS_TABLE).select("emails_sent, last_activity").execute()
    email_rows = emails_res.data or []

    timestamps = [
        _parse_iso(row.get("last_activity"))
        for row in email_rows
        if row.get("last_activity")
    ]
    timestamps = [t for t in timestamps if t is not None]

    total_emails = sum(int(row.get("emails_sent", 0) or 0) for row in email_rows)

    if timestamps and len(timestamps) > 1:
        total_seconds = (max(timestamps) - min(timestamps)).total_seconds() or 1
        emails_per_second = total_emails / total_seconds
    else:
        emails_per_second = float(total_emails)

    # Queue latency from outreach_leads next_followup
    queue_res = supabase.table(OUTREACH_LEADS_TABLE).select("next_followup, status").execute()
    queue_rows = queue_res.data or []

    future_queued_times = []
    for row in queue_rows:
        next_followup = _parse_iso(row.get("next_followup"))
        if next_followup:
            future_queued_times.append(next_followup)

    queue_latency = (now - min(future_queued_times)).total_seconds() if future_queued_times else 0

    # API response time from PERF_TABLE
    api_times = [
        row["execution_time"]
        for row in exec_rows
        if row.get("execution_time") is not None and "api" in (row.get("function_name") or "").lower()
    ]
    api_response_time = sum(api_times) / len(api_times) if api_times else 0

    return {
        "avg_send_time": round(avg_send_time, 3),
        "emails_per_second": round(emails_per_second, 3),
        "queue_latency": round(queue_latency, 3),
        "api_response_time": round(api_response_time, 3)
    }