# outreach_engine/core/ab_testing.py

from outreach_engine.database.supabase_client import supabase

AB_TABLE = "ab_testing"

# Record email variant performance
def record_variant_sent(campaign_id: int, variant_name: str):
    supabase.table(AB_TABLE).insert({
        "campaign_id": campaign_id,
        "variant": variant_name,
        "sent_count": 1,
        "open_count": 0,
        "click_count": 0,
        "reply_count": 0,
        "created_at": supabase.utcnow()
    }).on_conflict("campaign_id,variant").upsert({
        "sent_count": supabase.raw("ab_testing.sent_count + 1")
    }).execute()


def record_variant_open(campaign_id: int, variant_name: str):
    supabase.table(AB_TABLE).update({
        "open_count": supabase.raw("open_count + 1")
    }).eq("campaign_id", campaign_id).eq("variant", variant_name).execute()


def record_variant_click(campaign_id: int, variant_name: str):
    supabase.table(AB_TABLE).update({
        "click_count": supabase.raw("click_count + 1")
    }).eq("campaign_id", campaign_id).eq("variant", variant_name).execute()


def record_variant_reply(campaign_id: int, variant_name: str):
    supabase.table(AB_TABLE).update({
        "reply_count": supabase.raw("reply_count + 1")
    }).eq("campaign_id", campaign_id).eq("variant", variant_name).execute()