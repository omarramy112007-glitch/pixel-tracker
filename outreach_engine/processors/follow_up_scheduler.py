# outreach_engine/processors/follow_up_scheduler.py

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

from outreach_engine.processors.outreach_sender import send_email_async
from outreach_engine.processors.follow_up_manager import determine_next_step, update_followup
from outreach_engine.analytics.send_time_predictor import (
    predict_best_send_time,
    predict_reply_probability,
)
from outreach_engine.analytics.follow_up_rl import choose_action
from outreach_engine.database.supabase_client import supabase


# ---------------------------------------------------
# Follow-up delays (fallback)
# ---------------------------------------------------
FOLLOWUP_DELAYS = {0: 0, 1: 2, 2: 3, 3: 4, 4: 5}
MAX_STEP = max(FOLLOWUP_DELAYS.keys())


# ---------------------------------------------------
# Helpers
# ---------------------------------------------------
def _parse_datetime(value: Optional[str]) -> datetime:
    """
    Parse ISO datetime strings safely and return timezone-aware UTC datetime.
    """
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------
# Scheduler Logic (ULTRA AI + RL)
# ---------------------------------------------------
async def schedule_followups(leads: List[Dict], concurrency: int = 5, use_ai: bool = True):
    semaphore = asyncio.Semaphore(concurrency)

    async def check_and_send(lead: Dict):
        async with semaphore:
            email = lead.get("email")
            campaign_id = lead.get("campaign_id")

            if not email or not campaign_id:
                return

            status = (lead.get("status") or "").lower()
            followup_step = int(lead.get("followup_step") or 0)

            # ---------------------------------------------------
            # STOP CONDITIONS
            # ---------------------------------------------------
            # IMPORTANT:
            # Only leads with status == "sent" are eligible for follow-up.
            # replied / converted / opt-out must be skipped.
            if status != "sent":
                return

            if status in {"replied", "converted", "opt-out", "failed"}:
                return

            if followup_step >= MAX_STEP:
                return

            # ---------------------------------------------------
            # Determine next step
            # ---------------------------------------------------
            next_step = determine_next_step(email, campaign_id)
            if next_step == -1:
                return

            if next_step > MAX_STEP:
                return

            # Prevent duplicate sends for same lead + step
            lead_key = f"{email}:{next_step}"
            try:
                lock_exists = (
                    supabase.table("scheduler_locks")
                    .select("*")
                    .eq("lead_key", lead_key)
                    .execute()
                ).data

                if lock_exists:
                    return

                supabase.table("scheduler_locks").insert({
                    "lead_key": lead_key,
                    "instance_id": "INSTANCE_1",
                    "locked_at": _utcnow().isoformat()
                }).execute()

            except Exception as e:
                print(f"⚠ Scheduler lock error for {email}: {e}")
                return

            # ---------------------------------------------------
            # RL Decision
            # ---------------------------------------------------
            action = choose_action(lead)
            if action == "skip":
                print(f"🤖 RL skipped {email}")
                return
            elif action == "wait":
                print(f"🤖 RL delayed {email}")
                return

            # ---------------------------------------------------
            # AI Send-time + Reply Probability
            # ---------------------------------------------------
            if use_ai:
                next_send_time = predict_best_send_time(lead)
                reply_prob = predict_reply_probability(lead)
            else:
                last_sent = lead.get("last_email_sent") or lead.get("last_email_sent_at")
                delay_days = FOLLOWUP_DELAYS.get(next_step, 0)

                last_sent_dt = _parse_datetime(last_sent)
                next_send_time = last_sent_dt + timedelta(days=delay_days)
                reply_prob = 0.5

            # Make sure datetime comparison is safe
            if next_send_time.tzinfo is None:
                next_send_time = next_send_time.replace(tzinfo=timezone.utc)

            # Priority = expected revenue * reply probability
            lead["priority_score"] = (lead.get("expected_revenue", 0) or 0) * reply_prob

            # ---------------------------------------------------
            # Check if it's time to send
            # ---------------------------------------------------
            now = _utcnow()
            if now < next_send_time:
                return

            # ---------------------------------------------------
            # Send Email
            # ---------------------------------------------------
            try:
                sent = await send_email_async(email, campaign_id)
                if sent:
                    update_followup(email, campaign_id, step=next_step, status="sent")

                    lead["last_email_sent_at"] = now
                    lead["followup_step"] = next_step

                    # ---------------------------------------------------
                    # Logging
                    # ---------------------------------------------------
                    try:
                        supabase.table("lead_events").insert({
                            "lead_id": lead["id"],
                            "campaign_id": campaign_id,
                            "event_type": "ai_action",
                            "metadata": {
                                "action": action,
                                "priority_score": lead["priority_score"],
                                "predicted_revenue": lead.get("expected_revenue"),
                                "reply_probability": reply_prob,
                                "next_send_time": next_send_time.isoformat()
                            },
                            "created_at": now.isoformat()
                        }).execute()
                    except Exception as e:
                        print(f"⚠ Failed to log AI action for {email}: {e}")

            except Exception as e:
                print(f"❌ Error sending to {email}: {e}")

    tasks = [check_and_send(lead) for lead in leads]
    return await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------
# Auto Scheduler Loop
# ---------------------------------------------------
async def run_scheduler_periodically(
    leads: List[Dict],
    interval_minutes: int = 60,
    use_ai: bool = True
):
    while True:
        print(f"🕒 Scheduler running at {_utcnow().isoformat()} UTC")
        await schedule_followups(leads, concurrency=5, use_ai=use_ai)
        await asyncio.sleep(interval_minutes * 60)