#outreach_engine/analytics/rl_agent.py
import random

ACTIONS = ["send_now", "wait", "skip"]

def choose_action(lead: dict):
    # simple epsilon-greedy
    if random.random() < 0.2:
        return random.choice(ACTIONS)

    if lead.get("priority_score", 0) > 300:
        return "send_now"
    elif lead.get("priority_score", 0) > 100:
        return "wait"
    else:
        return "skip"

def reward_function(event: str):
    if event == "reply":
        return 10
    elif event == "conversion":
        return 50
    elif event == "no_response":
        return -2
    return 0