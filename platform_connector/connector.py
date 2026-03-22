# File: platform_connector/connector.py

from platform_connector.lead_manager import fetch_leads
from platform_connector.outreach_manager import run_outreach

class PlatformConnector:
    def __init__(self):
        self.leads = []

    async def update_leads(self):
        self.leads = await fetch_leads()

    def get_leads(self):
        return self.leads

    async def run_campaign(self, run_full: bool = False):
        """
        run_full = True → full autopilot
        run_full = False → initial outreach only
        """
        return await run_outreach(run_full=run_full)