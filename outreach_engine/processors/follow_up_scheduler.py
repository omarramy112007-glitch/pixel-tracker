# File: outreach_engine/processors/follow_up_scheduler.py

import asyncio
from datetime import datetime, timedelta
from typing import List, Dict

from outreach_engine.processors.outreach_sender import send_email_async
from outreach_engine.processors.follow_up_manager import determine_next_step, update_followup

from outreach_engine.analytics.send_time_predictor import predict_best_send_time, predict_reply_probability
from outreach_engine.analytics.follow_up_rl import choose_action
from outreach_engine.database.supabase_client import supabase

# ---------------------------------------------------
# Follow-up delays (fallback)
# ---------------------------------------------------
FOLLOWUP_DELAYS = {0: 0, 1: 2, 2: 3, 3: 4, 4: 5}
MAX_STEP = max(FOLLOWUP_DELAYS.keys())


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

            # ---------------- Stop conditions ----------------
            if lead.get("status") in ["replied", "opt-out"]:
                return

            # ---------------- Determine next step ----------------
            next_step = determine_next_step(email, campaign_id)
            if next_step == -1:
                return

            # ---------------- RL Decision ----------------
            action = choose_action(lead)
            if action == "skip":
                print(f"🤖 RL skipped {email}")
                return
            elif action == "wait":
                print(f"🤖 RL delayed {email}")
                return

            # ---------------- Multi-instance Lock ----------------
            lead_key = f"{email}:{next_step}"
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
                "locked_at": datetime.utcnow().isoformat()
            }).execute()

            # ---------------- AI Send-time + Priority ----------------
            if use_ai:
                next_send_time = predict_best_send_time(lead)
                reply_prob = predict_reply_probability(lead)
            else:
                last_sent = lead.get("last_email_sent_at")
                delay_days = FOLLOWUP_DELAYS.get(next_step, 0)

                if last_sent:
                    if isinstance(last_sent, str):
                        try:
                            last_sent = datetime.fromisoformat(last_sent.replace("Z", "+00:00"))
                        except Exception:
                            last_sent = datetime.utcnow()
                else:
                    last_sent = datetime.min

                next_send_time = last_sent + timedelta(days=delay_days)
                reply_prob = 0.5

            # Priority = expected revenue * reply probability
            lead["priority_score"] = lead.get("expected_revenue", 0) * reply_prob

            # ---------------- Check if it's time to send ----------------
            now = datetime.utcnow()
            if now < next_send_time:
                return

            # ---------------- Send Email ----------------
            try:
                sent = await send_email_async(email, campaign_id)
                if sent:
                    update_followup(email, campaign_id, step=next_step, status="sent")
                    lead["last_email_sent_at"] = datetime.utcnow()
                    lead["followup_step"] = next_step

                    # ---------------- Logging ----------------
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
                        "created_at": datetime.utcnow().isoformat()
                    }).execute()

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
        print(f"🕒 Scheduler running at {datetime.utcnow()} UTC")
        await schedule_followups(leads, concurrency=5, use_ai=use_ai)
        await asyncio.sleep(interval_minutes * 60)