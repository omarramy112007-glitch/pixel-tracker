# File: outreach_engine/analytics/funnel_analysis.py

from outreach_engine.database.event_repository import get_events  # 🔥 موجود في database

def followup_effectiveness(campaign_id: int):
    """
    Computes per-step follow-up conversion:
    - Emails sent per step
    - Replies per step
    - Conversion rate per step
    """

    events = get_events(campaign_id)  # 🔥 يجيب كل الأحداث (sent/replied)

    steps = {}

    for e in events:
        step = e.get("metadata", {}).get("step", 0)

        if step not in steps:
            steps[step] = {"sent": 0, "replied": 0}

        if e["event_type"] == "sent":
            steps[step]["sent"] += 1

        if e["event_type"] == "replied":
            steps[step]["replied"] += 1

    for step in steps:
        sent_count = steps[step]["sent"]
        replied_count = steps[step]["replied"]
        steps[step]["conversion"] = replied_count / sent_count if sent_count else 0

    return steps