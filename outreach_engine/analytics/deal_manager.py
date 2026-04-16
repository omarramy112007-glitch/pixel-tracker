# File: outreach_engine/analytics/deal_manager.py

from datetime import datetime
from outreach_engine.database.supabase_client import supabase
from outreach_engine.analytics.lead_scoring import score_lead

# ---------------------------------------------------
# Create Deal
# ---------------------------------------------------
def create_deal(lead_id: int, campaign_id: int, value: float):
    """
    Create a new deal (initially open)
    """

    result = supabase.table("deals").insert({
        "lead_id": lead_id,
        "campaign_id": campaign_id,
        "value": value,
        "status": "open",
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    print(f"📄 Deal created → Lead {lead_id} | Value={value}")

    return result.data


# ---------------------------------------------------
# Update Deal Status
# ---------------------------------------------------
def update_deal_status(deal_id: int, status: str):
    """
    Update deal status: open / won / lost
    """

    deal = (
        supabase.table("deals")
        .select("*")
        .eq("id", deal_id)
        .single()
        .execute()
        .data
    )

    if not deal:
        print(f"❌ Deal not found: {deal_id}")
        return {"error": "Deal not found"}

    # Update deal status
    supabase.table("deals").update({
        "status": status
    }).eq("id", deal_id).execute()

    print(f"🔄 Deal {deal_id} updated → {status}")

    # ---------------------------------------------------
    # WIN FLOW
    # ---------------------------------------------------
    if status == "won":

        lead_id = deal["lead_id"]
        value = deal["value"]
        campaign_id = deal["campaign_id"]

        # Update lead
        supabase.table("outreach_leads").update({
            "status": "converted",
            "deal_value": value,
            "converted_at": datetime.utcnow().isoformat()
        }).eq("id", lead_id).execute()

        # ---------------------------------------------------
        # ✅ FIXED: use event_repository ONLY
        # ---------------------------------------------------
        try:
            from outreach_engine.tracking.event_repository import store_event

            store_event(
                lead_id=lead_id,
                campaign_id=campaign_id,
                event_type="converted",
                metadata={
                    "value": value,
                    "source": "deal_closed"
                }
            )

        except Exception as e:
            print(f"⚠ Failed to track conversion event: {e}")

        # Re-score lead (AI learns revenue)
        try:
            lead = (
                supabase.table("outreach_leads")
                .select("*")
                .eq("id", lead_id)
                .single()
                .execute()
                .data
            )

            if lead:
                score_lead(lead)

        except Exception as e:
            print(f"⚠ Lead scoring failed: {e}")

        print(f"💰 Deal WON → Lead {lead_id} upgraded + tracked (value={value})")

    return {"success": True}