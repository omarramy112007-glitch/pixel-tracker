# outreach_engine/core/auto_optimizer.py

from apscheduler.schedulers.background import BackgroundScheduler
from outreach_engine.analytics.dashboard_data import get_all_campaigns_dashboard
from outreach_engine.core.campaign_manager import update_campaign

def auto_optimize_campaigns():
    campaigns = get_all_campaigns_dashboard()
    for c in campaigns:
        for rec in c["recommendations"]:
            if "Low open" in rec:
                # Swap subject line or sequence
                update_campaign(campaign_id=c.get("id"), subject_line="New Optimized Subject")
            elif "High replies" in rec:
                # Increase daily limit
                update_campaign(campaign_id=c.get("id"), daily_limit=c["emails_sent"] + 50)
            elif "High clicks" in rec:
                # Optional: trigger value-add email
                print(f"Campaign {c['campaign_name']}: Consider sending value-add email.")

# Schedule to run daily
scheduler = BackgroundScheduler()
scheduler.add_job(auto_optimize_campaigns, 'interval', hours=24)
scheduler.start()