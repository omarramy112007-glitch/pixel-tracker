# outreach_engine/analytics/metrics_calculator.py

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from outreach_engine.database.supabase_client import supabase

TABLE_NAME = "campaign_analytics"


# --------------------------------------------------
# UTILS
# --------------------------------------------------
def _coerce_number(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except:
        return 0.0


def _today():
    return date.today().isoformat()


# --------------------------------------------------
# UPSERT (🔥 CRITICAL FIX)
# --------------------------------------------------
def _upsert_metrics(campaign_id: int, updates: Dict[str, int]) -> None:
    """
    This is the missing piece in your system.
    Without this → dashboard will ALWAYS be zero.
    """
    try:
        day = _today()

        result = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
        )

        row = None

        # ✅ find today's row manually (fixes timestamp issue)
        if result.data:
            for r in result.data:
                if str(r.get("created_at", "")).startswith(day):
                    row = r
                    break

        if row:
            payload = {}

            for key, value in updates.items():
                payload[key] = int(_coerce_number(row.get(key)) + value)

            supabase.table(TABLE_NAME).update(payload).eq("id", row["id"]).execute()
            print(f"📊 UPDATED campaign metrics {campaign_id}: {payload}")

        else:
            payload = {
                "campaign_id": campaign_id,
                "emails_sent": 0,
                "opens": 0,
                "clicks": 0,
                "replies": 0,
                "conversions": 0,
                "replies_from_followups": 0,
                "created_at": day,
            }

            payload.update(updates)

            supabase.table(TABLE_NAME).insert(payload).execute()
            print(f"📊 CREATED campaign metrics {campaign_id}: {payload}")

    except Exception as e:
        print(f"❌ metrics upsert failed: {e}")


# --------------------------------------------------
# PUBLIC EVENT HOOKS (🔥 USE THESE)
# --------------------------------------------------
def record_sent(campaign_id: int):
    _upsert_metrics(campaign_id, {"emails_sent": 1})


def record_open(campaign_id: int):
    _upsert_metrics(campaign_id, {"opens": 1})


def record_click(campaign_id: int):
    _upsert_metrics(campaign_id, {"clicks": 1})


def record_reply(campaign_id: int):
    _upsert_metrics(campaign_id, {"replies": 1})


def record_conversion(campaign_id: int):
    _upsert_metrics(campaign_id, {"conversions": 1})


# --------------------------------------------------
# FETCH METRICS (FIXED)
# --------------------------------------------------
def get_metrics(campaign_id: int, day: Optional[str] = None) -> dict:
    if day is None:
        day = _today()

    try:
        result = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
        )

        if result.data:
            for row in result.data:
                if str(row.get("created_at", "")).startswith(day):
                    return {
                        "campaign_id": campaign_id,
                        "emails_sent": int(_coerce_number(row.get("emails_sent"))),
                        "opens": int(_coerce_number(row.get("opens"))),
                        "clicks": int(_coerce_number(row.get("clicks"))),
                        "replies": int(_coerce_number(row.get("replies"))),
                        "conversions": int(_coerce_number(row.get("conversions"))),
                        "replies_from_followups": int(_coerce_number(row.get("replies_from_followups"))),
                        "created_at": day,
                    }

        # fallback
        return {
            "campaign_id": campaign_id,
            "emails_sent": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
            "replies_from_followups": 0,
            "created_at": day,
        }

    except Exception as e:
        print(f"❌ get_metrics error: {e}")
        return {
            "campaign_id": campaign_id,
            "emails_sent": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
            "replies_from_followups": 0,
            "created_at": day,
        }


# --------------------------------------------------
# RATE CALCULATIONS
# --------------------------------------------------
def calculate_rates(metrics: dict) -> dict:
    sent = int(_coerce_number(metrics.get("emails_sent")))
    opens = int(_coerce_number(metrics.get("opens")))
    clicks = int(_coerce_number(metrics.get("clicks")))
    replies = int(_coerce_number(metrics.get("replies")))
    conversions = int(_coerce_number(metrics.get("conversions")))
    followups = int(_coerce_number(metrics.get("replies_from_followups")))

    return {
        "open_rate": round(opens / sent, 4) if sent else 0,
        "click_rate": round(clicks / sent, 4) if sent else 0,
        "reply_rate": round(replies / sent, 4) if sent else 0,
        "conversion_rate": round(conversions / sent, 4) if sent else 0,
        "followup_effectiveness": round(followups / replies, 4) if replies else 0,
    }