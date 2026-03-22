# outreach_engine/core/provider_config.py

PROVIDERS = {
    "sendgrid": {
        "daily_limit": 600,
        "sent_today": 0,
        "priority": 1
    },
    "postmark": {
        "daily_limit": 600,
        "sent_today": 0,
        "priority": 2
    },
    "smtp": {
        "daily_limit": 600,
        "sent_today": 0,
        "priority": 3
    }
}