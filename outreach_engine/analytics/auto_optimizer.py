#outreach_engine/analytics/auto_optimizer.py
from analytics.send_time_predictor import predict_reply_probability

def optimize_campaign(leads: list):
    for lead in leads:
        reply_prob = predict_reply_probability(lead)

        # High-value leads → more aggressive
        if reply_prob > 0.7:
            lead["send_more_followups"] = True
            lead["priority_boost"] = 1.5

        # Low-value leads → reduce effort
        elif reply_prob < 0.3:
            lead["reduce_sends"] = True
            lead["priority_boost"] = 0.7

        else:
            lead["priority_boost"] = 1.0

    return leads