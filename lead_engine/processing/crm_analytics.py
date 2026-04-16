# lead_engine/processing/crm_analytics.py

from statistics import mean
from typing import Dict, List, Any

from lead_engine.database.supabase_client import supabase
from lead_engine.core.retry import retry
from lead_engine.core.performance import timer


def _fetch_rows(table_name: str) -> List[Dict[str, Any]]:
    resp = supabase.table(table_name).select("*").execute()
    return resp.data if resp.data else []


@timer("Pipeline Summary")
@retry
def pipeline_summary() -> Dict:
    """
    Returns an overview of the pipeline.

    Uses outreach_leads as the primary table, with a fallback to leads.
    """
    try:
        leads = _fetch_rows("outreach_leads")
    except Exception:
        leads = _fetch_rows("leads")

    summary = {
        "total_leads": len(leads),
        "stages": {},
        "replied": 0,
        "meetings": 0,
        "won": 0,
        "lost": 0,
        "avg_deal_value": 0,
        "avg_automation_score": 0,
        "tech_stack_count": {},
        "open_count": 0,
        "click_count": 0,
        "reply_count": 0,
        "conversion_count": 0,
    }

    deal_values = []
    automation_scores = []
    tech_counter = {}

    for lead in leads:
        stage = lead.get("pipeline_stage", "Unknown")
        summary["stages"][stage] = summary["stages"].get(stage, 0) + 1

        summary["open_count"] += int(lead.get("open_count", 0) or 0)
        summary["click_count"] += int(lead.get("click_count", 0) or 0)
        summary["reply_count"] += int(lead.get("reply_count", 0) or 0)
        summary["conversion_count"] += int(lead.get("conversion_count", 0) or 0)

        if lead.get("reply_status") == "Replied":
            summary["replied"] += 1
        if lead.get("meeting_booked"):
            summary["meetings"] += 1

        if lead.get("deal_status") == "Won":
            summary["won"] += 1
            deal_values.append(lead.get("deal_value", 0) or 0)
        elif lead.get("deal_status") == "Lost":
            summary["lost"] += 1

        if lead.get("automation_score") is not None:
            automation_scores.append(lead["automation_score"])

        techs = lead.get("tech_stack") or []
        if isinstance(techs, str):
            techs = [techs]
        for t in techs:
            tech_counter[t] = tech_counter.get(t, 0) + 1

    summary["avg_deal_value"] = mean(deal_values) if deal_values else 0
    summary["avg_automation_score"] = mean(automation_scores) if automation_scores else 0
    summary["tech_stack_count"] = dict(sorted(tech_counter.items(), key=lambda x: x[1], reverse=True))

    return summary