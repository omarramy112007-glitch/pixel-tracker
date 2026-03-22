# outreach_engine/core/campaign_optimizer.py

from analytics.metrics_calculator import get_metrics, calculate_rates
from core.ab_testing import calculate_variant_performance
from core.campaign_manager import pause_campaign, resume_campaign
from datetime import datetime
from typing import Dict


# ---------------------------------------------------
# 1️⃣ Analyze Campaign and Suggest Optimizations
# ---------------------------------------------------
def optimize_campaign(campaign_id: int, threshold: Dict[str, float] = None) -> Dict[str, str]:
    """
    Automatically analyzes a campaign and suggests or applies optimizations.

    Parameters:
    - campaign_id: int
    - threshold: optional dict to override default thresholds
        Example:
            {"open_rate": 0.1, "click_rate": 0.05, "reply_rate": 0.05}

    Returns:
    - Dictionary with optimization actions
    """

    if threshold is None:
        threshold = {"open_rate": 0.1, "click_rate": 0.05, "reply_rate": 0.05}

    metrics = get_metrics(campaign_id)
    rates = calculate_rates(metrics)
    actions = {}

    # ---------------------------------------------------
    # 2️⃣ Check Open Rate
    # ---------------------------------------------------
    if rates["open_rate"] < threshold["open_rate"] * 100:
        actions["subject_line"] = "Change subject line to improve opens"

    # ---------------------------------------------------
    # 3️⃣ Check Click Rate
    # ---------------------------------------------------
    if rates["click_rate"] > threshold["click_rate"] * 100:
        actions["push_demo_email"] = "High click rate → push demo/case study email sooner"

    # ---------------------------------------------------
    # 4️⃣ Check Reply Rate
    # ---------------------------------------------------
    if rates["reply_rate"] > threshold["reply_rate"] * 100:
        actions["increase_send_volume"] = "High reply rate → increase daily send limit"

    # ---------------------------------------------------
    # 5️⃣ Check Variant Performance (A/B)
    # ---------------------------------------------------
    variant_perf = calculate_variant_performance(campaign_id)
    best_variant = None
    best_open_rate = 0

    for variant, stats in variant_perf.items():
        if stats["open_rate"] > best_open_rate:
            best_open_rate = stats["open_rate"]
            best_variant = variant

    if best_variant:
        actions["best_variant"] = f"Use variant {best_variant} as main subject line"

    return actions


# ---------------------------------------------------
# 6️⃣ Optional: Auto-adjust campaign based on actions
# ---------------------------------------------------
def apply_optimizations(campaign_id: int, actions: Dict[str, str]):
    """
    Apply automatic changes to the campaign.
    This can include:
    - Pausing/resuming campaigns
    - Updating templates or subject lines
    - Increasing daily send limits
    """

    if "subject_line" in actions:
        # Implement subject line change logic here
        print(f"🛠 Updating campaign {campaign_id} subject line")

    if "push_demo_email" in actions:
        # Adjust campaign sequence
        print(f"🛠 Rescheduling demo emails for campaign {campaign_id}")

    if "increase_send_volume" in actions:
        # Increase campaign daily limit
        print(f"🛠 Increasing daily send limit for campaign {campaign_id}")

    if "best_variant" in actions:
        # Promote best variant
        print(f"🛠 Setting best variant for campaign {campaign_id}: {actions['best_variant']}")