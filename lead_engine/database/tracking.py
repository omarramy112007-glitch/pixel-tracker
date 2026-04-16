# lead_engine/database/tracking.py

from datetime import datetime
from typing import Any, Dict, Optional
import hashlib
import json

from lead_engine.database.supabase_client import supabase
from outreach_engine.analytics.crm_analytics import update_crm_metrics

LEADS_TABLE = "outreach_leads"
EVENTS_TABLE = "lead_events"


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _canonical_event_type(event_type: str) -> str:
    return {
        "open": "open",
        "email_opened": "open",
        "click": "click",
        "link_clicked": "click",
        "reply": "reply",
        "email_sent": "sent",
        "deal": "conversion",
    }.get((event_type or "").lower().strip(), event_type)


def track_event(
    lead_id: int,
    event_type: str,
    metadata: Optional[Dict[str, Any]] = None,
    campaign_id: Optional[int] = None,
):
    try:
        event_key = _canonical_event_type(event_type)
        safe_metadata = _json_safe(metadata or {})

        # -------------------------------
        # 1️⃣ INSERT INTO lead_events
        # -------------------------------
        event_data = {
            "lead_id": lead_id,
            "campaign_id": campaign_id,
            "event_type": event_key,
            "metadata": safe_metadata,
            "created_at": datetime.utcnow().isoformat(),
        }

        supabase.table(EVENTS_TABLE).insert(event_data).execute()

        # -------------------------------
        # 2️⃣ UPDATE outreach_leads (counters)
        # -------------------------------
        lead = supabase.table(LEADS_TABLE).select("*").eq("id", lead_id).single().execute()

        if lead.data:
            updated = {}

            if event_key == "open":
                updated["open_count"] = (lead.data.get("open_count") or 0) + 1

            elif event_key == "click":
                updated["click_count"] = (lead.data.get("click_count") or 0) + 1

            elif event_key == "reply":
                updated["reply_count"] = (lead.data.get("reply_count") or 0) + 1

            elif event_key == "sent":
                updated["status"] = "sent"

            if updated:
                supabase.table(LEADS_TABLE).update(updated).eq("id", lead_id).execute()

        # -------------------------------
        # 3️⃣ UPDATE crm_analytics
        # -------------------------------
        if event_key == "open":
            update_crm_metrics(lead_id, opens=1, campaign_id=campaign_id)

        elif event_key == "click":
            update_crm_metrics(lead_id, clicks=1, campaign_id=campaign_id)

        elif event_key == "reply":
            update_crm_metrics(lead_id, replies=1, campaign_id=campaign_id)

        elif event_key == "sent":
            update_crm_metrics(lead_id, emails_sent=1, campaign_id=campaign_id)

        elif event_key == "conversion":
            update_crm_metrics(lead_id, conversions=1, campaign_id=campaign_id)

        print(f"✅ Event tracked: {event_key} | Lead {lead_id}")

    except Exception as e:
        print(f"❌ Tracking error: {e}")