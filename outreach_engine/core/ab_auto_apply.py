# outreach_engine/core/ab_auto_apply.py

from apscheduler.schedulers.background import BackgroundScheduler
from outreach_engine.analytics.dashboard_data import get_all_campaigns_dashboard
from outreach_engine.core.ab_selector import get_winning_variant
from outreach_engine.core.campaign_manager import update_campaign

def auto_apply_winning_variants():
    """
    Periodically selects the best-performing variant for each campaign
    and updates remaining leads to use the winning variant.
    """
    campaigns = get_all_campaigns_dashboard()
    
    for c in campaigns:
        winner = get_winning_variant(campaign_id=c.get("campaign_id"))
        if winner:
            update_campaign(
                campaign_id=c.get("campaign_id"),
                default_variant=winner
            )
            print(f"✅ Applied winning variant '{winner}' to campaign {c.get('campaign_name')}")

# Schedule to run every hour
scheduler = BackgroundScheduler()
scheduler.add_job(auto_apply_winning_variants, 'interval', hours=1)
scheduler.start()