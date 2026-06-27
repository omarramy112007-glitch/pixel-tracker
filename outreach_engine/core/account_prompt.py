# outreach_engine/core/account_prompt.py

from __future__ import annotations

from outreach_engine.database.supabase_client import supabase

SENDS_PER_NEW_ACCOUNT_PROMPT = 30


def get_total_sends_since_last_prompt() -> int:
    res = (
        supabase.table("system_config")
        .select("value")
        .eq("key", "sends_since_last_account_prompt")
        .limit(1)
        .execute()
    )
    if res.data:
        try:
            return int(res.data[0]["value"])
        except Exception:
            return 0
    return 0


def increment_sends_since_last_prompt() -> int:
    current = get_total_sends_since_last_prompt() + 1
    supabase.table("system_config").upsert({
        "key": "sends_since_last_account_prompt",
        "value": str(current),
    }, on_conflict="key").execute()
    return current


def reset_sends_counter() -> None:
    supabase.table("system_config").upsert({
        "key": "sends_since_last_account_prompt",
        "value": "0",
    }, on_conflict="key").execute()


def should_pause_for_new_account() -> bool:
    return get_total_sends_since_last_prompt() >= SENDS_PER_NEW_ACCOUNT_PROMPT
