# outreach_engine/core/system_monitor.py

from datetime import datetime, date
from outreach_engine.database.supabase_client import supabase

# -------------------------------
# Tables
# -------------------------------
CAMPAIGN_TABLE = "campaigns"
ANALYTICS_TABLE = "campaign_analytics"
FAILURE_TABLE = "system_failures"
QUEUE_TABLE = "email_queue"
PERF_TABLE = "system_performance"

# --------------------------------------------------
# 1️⃣ System Health Overview
# --------------------------------------------------
def get_system_health() -> dict:
    """
    Returns overall system health metrics.
    Used by dashboards, monitoring tools, health checks.
    """
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Emails sent today
    sent_res = supabase.table(ANALYTICS_TABLE)\
        .select("emails_sent")\
        .gte("created_at", today_start.isoformat())\
        .execute()
    emails_sent_today = sum(row.get("emails_sent", 0) for row in (sent_res.data or []))

    # Active campaigns
    campaigns_res = supabase.table(CAMPAIGN_TABLE)\
        .select("id")\
        .eq("status", "active")\
        .execute()
    active_campaigns = len(campaigns_res.data or [])

    # Queue size
    queue_res = supabase.table(QUEUE_TABLE).select("id").execute()
    queue_size = len(queue_res.data or [])

    # Failed emails
    failures_res = supabase.table(FAILURE_TABLE).select("id").execute()
    failed_emails = len(failures_res.data or [])

    # Avg send time placeholder (from performance metrics)
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

# --------------------------------------------------
# 2️⃣ Campaign Status
# --------------------------------------------------
def get_campaign_status() -> list:
    """
    Returns list of all campaigns with id, name, status.
    """
    res = supabase.table(CAMPAIGN_TABLE).select("id, name, status").execute()
    return res.data or []

# --------------------------------------------------
# 3️⃣ Queue Status
# --------------------------------------------------
def get_queue_status() -> dict:
    """
    Returns queue size and status.
    """
    res = supabase.table(QUEUE_TABLE).select("id").execute()
    queue_size = len(res.data or [])
    return {
        "queue_size": queue_size,
        "status": "healthy" if queue_size < 1000 else "high_load"
    }

# --------------------------------------------------
# 4️⃣ Performance Metrics
# --------------------------------------------------
def get_performance_metrics() -> dict:
    """
    Returns avg send time, emails/sec, queue latency, API response time
    """
    now = datetime.utcnow()

    # Avg execution time
    exec_res = supabase.table(PERF_TABLE).select("execution_time, function_name").execute()
    exec_times = [row["execution_time"] for row in (exec_res.data or []) if row.get("execution_time")]
    avg_send_time = sum(exec_times)/len(exec_times) if exec_times else 0

    # Emails per second
    emails_res = supabase.table(ANALYTICS_TABLE).select("emails_sent, created_at").execute()
    timestamps = [datetime.fromisoformat(row["created_at"]) for row in (emails_res.data or []) if row.get("created_at")]
    total_emails = sum(row.get("emails_sent", 0) for row in (emails_res.data or []))
    if timestamps and len(timestamps) > 1:
        total_seconds = (max(timestamps) - min(timestamps)).total_seconds() or 1
        emails_per_second = total_emails / total_seconds
    else:
        emails_per_second = total_emails  # If only one timestamp, approximate

    # Queue latency
    queue_res = supabase.table(QUEUE_TABLE).select("queued_at").execute()
    queue_times = [datetime.fromisoformat(row["queued_at"]) for row in (queue_res.data or []) if row.get("queued_at")]
    queue_latency = (now - min(queue_times)).total_seconds() if queue_times else 0

    # API response time (approx from PERF_TABLE)
    api_times = [row["execution_time"] for row in (exec_res.data or []) if "api" in (row.get("function_name") or "").lower()]
    api_response_time = sum(api_times)/len(api_times) if api_times else 0

    return {
        "avg_send_time": round(avg_send_time, 3),
        "emails_per_second": round(emails_per_second, 3),
        "queue_latency": round(queue_latency, 3),
        "api_response_time": round(api_response_time, 3)
    }