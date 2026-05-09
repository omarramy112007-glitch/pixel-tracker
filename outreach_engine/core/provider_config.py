# outreach_engine/core/provider_config.py

PROVIDERS = {
    "gmail": {
        "daily_limit": 450,
        "sent_today": 0,
        "priority": 1,
        "enabled": True,
    },
    "sendgrid": {
        "daily_limit": 600,
        "sent_today": 0,
        "priority": 2,
        "enabled": False,
    },
    "postmark": {
        "daily_limit": 600,
        "sent_today": 0,
        "priority": 3,
        "enabled": False,
    },
    "smtp": {
        "daily_limit": 600,
        "sent_today": 0,
        "priority": 4,
        "enabled": False,
    },
}