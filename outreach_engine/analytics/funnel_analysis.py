# outreach_engine/analytics/funnel_analysis.py

from outreach_engine.tracking.event_repository import get_campaign_events


def followup_effectiveness(campaign_id: int):
    events = get_campaign_events(campaign_id)

    steps = {}

    for e in events:
        metadata = e.get("metadata") or {}
        step = metadata.get("step", 0)

        if step not in steps:
            steps[step] = {"sent": 0, "replied": 0}

        et = (e.get("event_type") or "").lower()

        if et in ["sent", "email_sent"]:
            steps[step]["sent"] += 1

        elif et in ["replied", "reply"]:
            steps[step]["replied"] += 1

    for step in steps:
        sent = steps[step]["sent"]
        replied = steps[step]["replied"]

        steps[step]["conversion"] = replied / sent if sent else 0

    return steps