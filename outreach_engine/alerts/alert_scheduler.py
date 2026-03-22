# File: outreach_engine/alerts/alert_scheduler.py

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from outreach_engine.analytics.alert_monitor import check_campaigns_alerts

scheduler = AsyncIOScheduler()

async def start_scheduler():
    """
    Async scheduler for campaign alerts
    """
    scheduler.add_job(check_campaigns_alerts, 'interval', minutes=5)
    scheduler.start()
    print("🚀 Async Alert scheduler started: checking campaigns every 5 minutes")

# To run in main app:
# asyncio.create_task(start_scheduler())