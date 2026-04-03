# outreach_engine/analytics/lead_scoring.py

from datetime import datetime
from outreach_engine.database.supabase_client import supabase

# ---------------- Core AI ----------------
from outreach_engine.analytics.revenue_predictor import expected_revenue
from outreach_engine.analytics.send_time_predictor import predict_reply_probability

# ---------------- Phase 17+ ULTRA ----------------
from outreach_engine.analytics.ml_revenue_model import predict_revenue_ml
from outreach_engine.analytics.pricing_optimizer import adjust_pricing


# ---------------------------------------------------
# Core AI Scoring Logic (ULTRA)
# ---------------------------------------------------
def calculate_engagement_score(lead: dict) -> float:
    score = 0.0

    # ---------------- Engagement ----------------
    if lead.get("email_opened"):
        score += 2
    if lead.get("link_clicked"):
        score += 4
    if lead.get("status") == "replied":
        score += 6
    if lead.get("status") == "converted":
        score += 20

    # ---------------- Deal Value ----------------
    if lead.get("deal_value"):
        score += float(lead["deal_value"]) / 100

    # ---------------- Company ----------------
    size_weight = {"small": 1, "medium": 2, "large": 3}
    score += size_weight.get((lead.get("company_size") or "small"), 1) * 2

    industry_weight = {"tech": 3, "finance": 2, "other": 1}
    score += industry_weight.get((lead.get("industry") or "other"), 1) * 2

    role_weight = 5 if (lead.get("role") or "").lower() in ["ceo", "cto", "founder", "manager"] else 2
    score += role_weight

    # ---------------- Response Speed ----------------
    response_time = lead.get("avg_response_hours", 48)
    score += max(0, 24 - min(response_time, 24))

    if lead.get("touch_count", 1) == 1:
        score *= 1.2

    # ---------------- Expected Revenue ----------------
    lead["expected_revenue"] = expected_revenue(lead)

    # ---------------- ML Revenue (ULTRA) ----------------
    lead["ml_revenue"] = predict_revenue_ml(lead)

    # ---------------- Pricing Engine ----------------
    lead["price"] = adjust_pricing(lead)

    # ---------------- Reply Probability ----------------
    reply_prob = predict_reply_probability(lead)

    # ---------------- Priority Score ----------------
    lead["priority_score"] = lead["ml_revenue"] * reply_prob

    # ---------------- Final Score Boost ----------------
    score += lead["expected_revenue"] / 50
    score += lead["ml_revenue"] / 100

    # ---------------- Normalize ----------------
    score = min(max(score, 0), 100)

    return score


# ---------------------------------------------------
# Store Score (SAFE)
# ---------------------------------------------------
def update_lead_score(lead_id: int, score: float):
    if not lead_id:
        print("❌ Cannot update lead: missing ID, skipping.")
        return

    supabase.table("crm_analytics").upsert({
        "lead_id": lead_id,
        "engagement_score": score
    }).execute()


# ---------------------------------------------------
# Full Pipeline
# ---------------------------------------------------
def score_lead(lead: dict):
    score = calculate_engagement_score(lead)

    lead_id = lead.get("id")
    if not lead_id:
        print("⚠ Skipping lead without ID:", lead)
        return score

    update_lead_score(lead_id, score)
    return score


# ---------------------------------------------------
# Bulk Campaign Scoring (ULTRA PRIORITY)
# ---------------------------------------------------
def score_campaign_leads(campaign_id: int, min_score: float = 0):
    leads = (
        supabase.table("outreach_leads")
        .select("*")
        .eq("campaign_id", campaign_id)
        .execute().data
    ) or []

    scored_leads = []

    for lead in leads:
        lead["engagement_score"] = score_lead(lead)
        if lead["engagement_score"] >= min_score:
            scored_leads.append(lead)

    # ---------------- Daily Ranking ----------------
    today = datetime.utcnow().date()

    ranking_data = [
        {
            "lead_id": l.get("id"),
            "campaign_id": campaign_id,
            "score": l["engagement_score"],
            "created_at": str(today)
        }
        for l in scored_leads
        if l.get("id")
    ]

    if ranking_data:
        supabase.table("lead_ranking").insert(ranking_data).execute()

    # ---------------- Sort by ULTRA Priority ----------------
    scored_leads = sorted(
        scored_leads,
        key=lambda l: l.get("priority_score", 0),
        reverse=True
    )

    return scored_leads


# ---------------------------------------------------
# Rank Leads (ULTRA)
# ---------------------------------------------------
def rank_leads_by_expected_revenue(leads: list):
    for lead in leads:
        lead["expected_revenue"] = expected_revenue(lead)
        lead["ml_revenue"] = predict_revenue_ml(lead)
        reply_prob = predict_reply_probability(lead)
        lead["priority_score"] = lead["ml_revenue"] * reply_prob

    return sorted(
        leads,
        key=lambda l: l.get("priority_score", 0),
        reverse=True
    )