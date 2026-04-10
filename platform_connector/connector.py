# File: platform_connector/connector.py

from platform_connector.lead_manager import fetch_leads
from platform_connector.outreach_manager import run_outreach


class PlatformConnector:
    def __init__(self):
        self.leads = []

    async def update_leads(self):
        """
        Fetch leads and store them
        """
        self.leads = await fetch_leads()
        return self.leads

    def get_leads(self):
        return self.leads

    async def run_campaign(self, run_full: bool = False):
        """
        run_full = True → full autopilot
        run_full = False → initial outreach only
        """
        # Refresh leads first so the campaign has real data
        await self.update_leads()

        # Run the outreach engine using the supported interface
        result = await run_outreach(run_full=run_full)

        return {
            "status": "completed",
            "mode": "full" if run_full else "initial",
            "total_leads": len(self.leads),
            "result": result,
        }  # ✅ removed the extra parenthesis