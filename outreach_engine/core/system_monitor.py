# outreach_engine/core/system_monitor.py

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from outreach_engine.database.supabase_client import supabase

CAMPAIGN_TABLE = "campaigns"
ANALYTICS_TABLE = "crm_analytics"
EVENTS_TABLE = "lead_events"
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


# --------------------------------------------------
# SYSTEM HEALTH (FIXED)
# --------------------------------------------------
def get_system_health() -> dict:
    now = datetime.utcnow()
    today_start = _today_start()

    # ✅ USE EVENTS TABLE (NOT CRM)
    events_res = (
        supabase.table(EVENTS_TABLE)
        .select("event_type, timestamp")
        .eq("event_type", "sent")
        .gte("timestamp", today_start.isoformat())
        .execute()
    )

    emails_sent_today = len(events_res.data or [])

    # Active campaigns
    campaigns_res = (
        supabase.table(CAMPAIGN_TABLE)
        .select("id")
        .eq("active", True)
        .execute()
    )
    active_campaigns = len(campaigns_res.data or [])

    # Queue size
    leads_res = (
        supabase.table(OUTREACH_LEADS_TABLE)
        .select("id, status")
        .execute()
    )
    leads_rows = leads_res.data or []

    pending_statuses = {"pending", "new", "not_contacted"}
    queue_size = sum(
        1 for row in leads_rows
        if (row.get("status") or "").lower().strip() in pending_statuses
    )

    # Failures
    failures_res = supabase.table(FAILURE_TABLE).select("id").execute()
    failed_emails = len(failures_res.data or [])

    perf_metrics = get_performance_metrics()

    return {
        "emails_sent_today": emails_sent_today,
        "active_campaigns": active_campaigns,
        "queue_size": queue_size,
        "failed_emails": failed_emails,
        "avg_send_time": perf_metrics.get("avg_send_time", 0),
        "timestamp": now.isoformat()
    }


# --------------------------------------------------
# PERFORMANCE METRICS (FIXED)
# --------------------------------------------------
def get_performance_metrics() -> dict:
    now = datetime.utcnow()

    # Execution time
    exec_res = supabase.table(PERF_TABLE).select("execution_time, function_name").execute()
    exec_rows = exec_res.data or []

    exec_times = [
        row["execution_time"]
        for row in exec_rows
        if row.get("execution_time") is not None
    ]
    avg_send_time = sum(exec_times) / len(exec_times) if exec_times else 0

    # ✅ USE EVENTS FOR THROUGHPUT
    events_res = (
        supabase.table(EVENTS_TABLE)
        .select("timestamp")
        .eq("event_type", "sent")
        .execute()
    )

    event_rows = events_res.data or []

    timestamps = [
        _parse_iso(row.get("timestamp"))
        for row in event_rows
        if row.get("timestamp")
    ]
    timestamps = [t for t in timestamps if t is not None]

    total_emails = len(timestamps)

    if timestamps and len(timestamps) > 1:
        total_seconds = (max(timestamps) - min(timestamps)).total_seconds() or 1
        emails_per_second = total_emails / total_seconds
    else:
        emails_per_second = float(total_emails)

    # Queue latency
    queue_res = supabase.table(OUTREACH_LEADS_TABLE).select("next_followup").execute()
    queue_rows = queue_res.data or []

    future_times = []
    for row in queue_rows:
        t = _parse_iso(row.get("next_followup"))
        if t:
            future_times.append(t)

    queue_latency = (now - min(future_times)).total_seconds() if future_times else 0

    # API time
    api_times = [
        row["execution_time"]
        for row in exec_rows
        if row.get("execution_time") is not None
        and "api" in (row.get("function_name") or "").lower()
    ]
    api_response_time = sum(api_times) / len(api_times) if api_times else 0

    return {
        "avg_send_time": round(avg_send_time, 3),
        "emails_per_second": round(emails_per_second, 3),
        "queue_latency": round(queue_latency, 3),
        "api_response_time": round(api_response_time, 3),
    }