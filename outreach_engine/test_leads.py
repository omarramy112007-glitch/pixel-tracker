from platform_connector.lead_manager import fetch_leads
import asyncio

leads = asyncio.run(fetch_leads())

print(f"\nTotal leads: {len(leads)}\n")

for lead in leads[:5]:
    print(
        lead.get("name"),
        "|",
        lead.get("title"),
        "|",
        lead.get("industry"),
        "|",
        lead.get("email")
    )