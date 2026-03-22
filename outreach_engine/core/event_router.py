# outreach_engine/core/event_router.py

from typing import Optional, Dict
from outreach_engine.analytics.campaign_analytics import (
    record_email_sent,
    record_open,
    record_click,
    record_reply,
    record_conversion
)
from outreach_engine.analytics.crm_analytics import update_crm_metrics

def handle_event(
    event_type: str,
    campaign_id: int,
    lead_id: Optional[int] = None,
    metadata: Optional[Dict] = None
):
    """
    Route system events to analytics and CRM.

    Parameters
    ----------
    event_type : str
        Type of event: sent, opened, clicked, replied, converted
    campaign_id : int
        Campaign associated with the event
    lead_id : int, optional
        ID of the lead who triggered the event
    metadata : dict, optional
        Additional info (IP, user_agent, timestamp, etc.)

    Notes
    -----
    All events automatically update:
        1️⃣ campaign_analytics table
        2️⃣ crm_analytics table
    """

    # Default metadata dict
    if metadata is None:
        metadata = {}

    # -----------------------------
    # 1️⃣ Campaign Analytics Update
    # -----------------------------
    if event_type == "sent":
        record_email_sent(campaign_id, lead_id=lead_id, metadata=metadata)
        if lead_id:
            update_crm_metrics(lead_id, emails_sent=1, last_activity=metadata.get("timestamp"))

    elif event_type == "opened":
        record_open(campaign_id, lead_id=lead_id, metadata=metadata)
        if lead_id:
            update_crm_metrics(lead_id, opens=1, last_activity=metadata.get("timestamp"))

    elif event_type == "clicked":
        record_click(campaign_id, lead_id=lead_id, metadata=metadata)
        if lead_id:
            update_crm_metrics(lead_id, clicks=1, last_activity=metadata.get("timestamp"))

    elif event_type == "replied":
        record_reply(campaign_id, lead_id=lead_id, metadata=metadata)
        if lead_id:
            update_crm_metrics(lead_id, replies=1, last_activity=metadata.get("timestamp"))

    elif event_type == "converted":
        record_conversion(campaign_id, lead_id=lead_id, metadata=metadata)
        if lead_id:
            update_crm_metrics(lead_id, conversions=1, last_activity=metadata.get("timestamp"))

    else:
        print(f"⚠ Unknown event type: {event_type}")