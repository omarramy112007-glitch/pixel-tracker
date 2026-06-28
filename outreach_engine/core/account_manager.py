# outreach_engine/core/account_manager.py

from __future__ import annotations

import base64
import json
from datetime import date
from typing import Any, Dict, List, Optional

from outreach_engine.database.supabase_client import supabase


def _decode_token_b64(token_b64: str) -> Dict[str, Any]:
    return json.loads(base64.b64decode(token_b64).decode("utf-8"))


def _reset_daily_counter_if_needed(account: Dict[str, Any]) -> Dict[str, Any]:
    today = date.today().isoformat()
    if str(account.get("last_reset_date")) != today:
        supabase.table("sending_accounts").update({
            "sent_today": 0,
            "last_reset_date": today,
        }).eq("account_key", account["account_key"]).execute()
        account["sent_today"] = 0
        account["last_reset_date"] = today
    return account


# outreach_engine/core/account_manager.py — only get_active_accounts changes

def get_active_accounts() -> List[Dict[str, Any]]:
    res = (
        supabase.table("sending_accounts")
        .select("*")
        .eq("is_active", True)
        .execute()
    )
    accounts = res.data or []
    accounts = [_reset_daily_counter_if_needed(a) for a in accounts]

    # Decode the token for every account here so every caller
    # (gmail_watcher, gmail_webhook, outreach_sender) can rely on
    # _decoded_token always being present — this was previously only
    # added in get_next_available_account() and get_account_by_key(),
    # which is why check_for_replies() was crashing with KeyError.
    for account in accounts:
        try:
            account["_decoded_token"] = _decode_token_b64(account["token_b64"])
        except Exception as e:
            print(f"⚠ Failed to decode token for account {account.get('account_key')}: {e}")
            account["_decoded_token"] = None

    return accounts
def get_next_available_account() -> Optional[Dict[str, Any]]:
    accounts = get_active_accounts()
    eligible = [
        a for a in accounts
        if int(a.get("sent_today") or 0) < int(a.get("daily_send_cap") or 30)
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda a: int(a.get("sent_today") or 0))
    chosen = eligible[0]
    chosen["_decoded_token"] = _decode_token_b64(chosen["token_b64"])
    return chosen


def get_account_by_key(account_key: str) -> Optional[Dict[str, Any]]:
    res = (
        supabase.table("sending_accounts")
        .select("*")
        .eq("account_key", account_key)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    account = res.data[0]
    account["_decoded_token"] = _decode_token_b64(account["token_b64"])
    return account


def increment_sent_count(account_key: str) -> None:
    res = (
        supabase.table("sending_accounts")
        .select("sent_today")
        .eq("account_key", account_key)
        .limit(1)
        .execute()
    )
    current = int(res.data[0].get("sent_today") or 0) if res.data else 0
    supabase.table("sending_accounts").update({
        "sent_today": current + 1,
    }).eq("account_key", account_key).execute()


def add_account(
    account_key: str,
    email_address: str,
    token_b64: str,
    daily_send_cap: int = 30,
) -> None:
    supabase.table("sending_accounts").upsert({
        "account_key": account_key,
        "email_address": email_address,
        "token_b64": token_b64,
        "is_active": True,
        "daily_send_cap": daily_send_cap,
        "sent_today": 0,
        "last_reset_date": date.today().isoformat(),
    }, on_conflict="account_key").execute()
