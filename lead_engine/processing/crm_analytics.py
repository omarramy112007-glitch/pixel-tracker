# processing/crm_analytics.py

from database.supabase_client import supabase
from typing import Dict
from statistics import mean
from core.retry import retry
from core.performance import timer

@timer("Pipeline Summary")
@retry
def update_crm_metrics() -> Dict:
    """
    Returns an overview of your pipeline: stages, conversion, reply/meeting rates.
    Wrapped with retry & performance timer for Phase 11.
    """
    resp = supabase.table("leads").select("*").execute()
    leads = resp.data if resp.data else []

    summary = {
        "total_leads": len(leads),
        "stages": {},
        "replied": 0,
        "meetings": 0,
        "won": 0,
        "lost": 0,
        "avg_deal_value": 0,
        "avg_automation_score": 0,
        "tech_stack_count": {}
    }

    deal_values = []
    automation_scores = []
    tech_counter = {}

    for lead in leads:
        stage = lead.get("pipeline_stage", "Unknown")
        summary["stages"][stage] = summary["stages"].get(stage, 0) + 1

        if lead.get("reply_status") == "Replied":
            summary["replied"] += 1
        if lead.get("meeting_booked"):
            summary["meetings"] += 1

        if lead.get("deal_status") == "Won":
            summary["won"] += 1
            deal_values.append(lead.get("deal_value", 0))
        elif lead.get("deal_status") == "Lost":
            summary["lost"] += 1

        if lead.get("automation_score") is not None:
            automation_scores.append(lead["automation_score"])

        techs = lead.get("tech_stack") or []
        for t in techs:
            tech_counter[t] = tech_counter.get(t, 0) + 1

    summary["avg_deal_value"] = mean(deal_values) if deal_values else 0
    summary["avg_automation_score"] = mean(automation_scores) if automation_scores else 0
    summary["tech_stack_count"] = dict(sorted(tech_counter.items(), key=lambda x: x[1], reverse=True))

    return summary