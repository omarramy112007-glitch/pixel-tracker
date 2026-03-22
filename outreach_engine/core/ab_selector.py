# outreach_engine/core/ab_selector.py
from outreach_engine.database.supabase_client import supabase

AB_TABLE = "ab_testing"

def get_winning_variant(campaign_id: int, min_sent_threshold: int = 50):
    """
    Selects the best variant based on reply rate or engagement.
    Only considers variants with at least `min_sent_threshold` emails sent.
    """
    variants = supabase.table(AB_TABLE).select("*").eq("campaign_id", campaign_id).execute().data

    if not variants:
        return None

    # Only consider variants above threshold
    eligible = [v for v in variants if v["sent_count"] >= min_sent_threshold]
    if not eligible:
        return None

    # Determine winner: highest reply rate (or clicks+opens as tie-breaker)
    for v in eligible:
        v["reply_rate"] = v["reply_count"] / max(v["sent_count"], 1)
        v["engagement_rate"] = (v["click_count"] + v["open_count"]) / max(v["sent_count"], 1)

    winner = max(eligible, key=lambda x: (x["reply_rate"], x["engagement_rate"]))

    return winner["variant"]