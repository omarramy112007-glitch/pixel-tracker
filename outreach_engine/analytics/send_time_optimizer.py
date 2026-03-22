# analytics/send_time_optimizer.py

from collections import defaultdict
from tracking.event_repository import get_events


def best_send_hour(campaign_id: int):

    events = get_events(campaign_id)

    hour_stats = defaultdict(int)

    for e in events:
        if e["event_type"] == "replied":
            hour = e["timestamp"].hour
            hour_stats[hour] += 1

    if not hour_stats:
        return 9  # default

    return max(hour_stats, key=hour_stats.get)