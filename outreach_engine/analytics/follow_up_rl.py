# File: outreach_engine/analytics/follow_up_rl.py

import numpy as np
from datetime import datetime, timedelta

# ---------------------------------------------------
# Simple RL placeholder (can evolve to Q-learning / policy gradient)
# ---------------------------------------------------
Q_TABLE = {}  # Placeholder for Q-values or learned policy

def choose_action(lead: dict) -> str:
    """
    Decide the next action for a follow-up:
        - "send_now"
        - "wait"
        - "skip"

    Inputs considered:
        - priority_score (higher → more urgent)
        - last_email_sent_at
        - reply_history / status
    """
    score = lead.get("priority_score", 0)
    last_sent = lead.get("last_email_sent_at")
    responded = lead.get("status") == "replied"

    # Already responded → skip
    if responded:
        return "skip"

    # High priority → send now
    if score > 50:
        return "send_now"

    # If recently sent → wait
    if last_sent:
        # Optional: add dynamic wait based on hours since last email
        hours_since = (datetime.utcnow() - last_sent).total_seconds() / 3600
        if hours_since < 24:
            return "wait"

    # Default fallback → send
    return "send_now"