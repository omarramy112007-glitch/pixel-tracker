# outreach_engine/core/campaign_manager.py

from typing import Dict, List, Optional
from datetime import datetime

from outreach_engine.database.supabase_client import supabase
from core.lead_manager import add_or_update_lead, bulk_add_or_update

# ---------------------------------------------------
# Create Campaign
# ---------------------------------------------------
def create_campaign(campaign: Dict) -> Dict:
    """
    Create a new outreach campaign.

    Expected fields:
    - name
    - template_set
    - target_industry
    - daily_limit
    - active
    """

    payload = {
        "name": campaign.get("name"),
        "template_set": campaign.get("template_set"),
        "target_industry": campaign.get("target_industry"),
        "daily_limit": campaign.get("daily_limit", 100),
        "active": campaign.get("active", True),
        "created_at": datetime.utcnow().isoformat()
    }

    result = supabase.table("campaigns").insert(payload).execute()
    return result.data

# ---------------------------------------------------
# Update Campaign (for auto-optimizer / manual edits)
# ---------------------------------------------------
def update_campaign(campaign_id: int, **kwargs) -> None:
    """
    Update campaign attributes dynamically.
    Examples:
        - update_campaign(1, daily_limit=200)
        - update_campaign(1, subject_line="New Subject")
    """
    if not kwargs:
        return
    supabase.table("campaigns").update(kwargs).eq("id", campaign_id).execute()

# ---------------------------------------------------
# Get Active Campaigns
# ---------------------------------------------------
def get_active_campaigns() -> List[Dict]:
    """
    Return all currently active campaigns.
    """
    result = (
        supabase
        .table("campaigns")
        .select("*")
        .eq("active", True)
        .execute()
    )
    return result.data or []

# ---------------------------------------------------
# Assign Leads To Campaign (Deduplication Safe)
# ---------------------------------------------------
def assign_leads_to_campaign(
    campaign_id: int,
    lead_list: List[Dict]
) -> None:
    """
    Assign leads to a campaign and store in outreach_leads.

    Each lead should be a dict with at least:
    - email
    Optional: first_name, last_name, company, metadata

    Dedup-safe: existing leads will be updated, new leads inserted.
    """
    bulk_add_or_update(lead_list, campaign_id)

# ---------------------------------------------------
# Pause Campaign
# ---------------------------------------------------
def pause_campaign(campaign_id: int) -> None:
    """
    Pause a campaign.
    """
    supabase.table("campaigns").update({"active": False}).eq("id", campaign_id).execute()

# ---------------------------------------------------
# Resume Campaign
# ---------------------------------------------------
def resume_campaign(campaign_id: int) -> None:
    """
    Resume a paused campaign.
    """
    supabase.table("campaigns").update({"active": True}).eq("id", campaign_id).execute()

# ---------------------------------------------------
# Get Campaign For Lead
# ---------------------------------------------------
def get_campaign_for_lead(lead_id: int) -> Optional[Dict]:
    """
    Return the campaign assigned to a specific lead.
    """
    result = (
        supabase
        .table("campaign_leads")
        .select("campaigns(*)")
        .eq("lead_id", lead_id)
        .execute()
    )
    if result.data:
        return result.data[0]["campaigns"]
    return None