from fastapi import FastAPI, APIRouter
from typing import Dict, Any
from outreach_engine.database.supabase_client import supabase

app = FastAPI(title="Outreach Dashboard API")
router = APIRouter()

def safe_int(val):
    try:
        return int(val or 0)
    except:
        return 0

@router.get("/dashboard/campaigns/{campaign_id}")
def get_campaign_dashboard(campaign_id: int) -> Dict[str, Any]:
    try:
        print(f"Fetching campaign {campaign_id} leads...")
        res = supabase.table("outreach_leads").select("*").eq("campaign_id", campaign_id).execute()
        print("Supabase response:", res)
        leads = res.data or []

        total_leads = len(leads)
        emails_sent = len([l for l in leads if (l.get("status") or "").lower() in ["sent","replied","converted"]])
        opens = sum(safe_int(l.get("open_count")) for l in leads)
        clicks = sum(safe_int(l.get("click_count")) for l in leads)
        replies = sum(safe_int(l.get("reply_count")) for l in leads)
        conversions = sum(safe_int(l.get("conversion_count")) for l in leads)

        open_rate = (opens / emails_sent) if emails_sent else 0
        click_rate = (clicks / emails_sent) if emails_sent else 0
        reply_rate = (replies / emails_sent) if emails_sent else 0
        conversion_rate = (conversions / emails_sent) if emails_sent else 0

        recommendations = []
        if emails_sent > 0:
            if open_rate < 0.3:
                recommendations.append("Low open rate → improve subject lines")
            if reply_rate < 0.1:
                recommendations.append("Low reply rate → improve email body / CTA")
            if conversion_rate < 0.05:
                recommendations.append("Low conversion → improve offer / landing")

        return {
            "campaign_name": f"Campaign {campaign_id}",
            "total_leads": total_leads,
            "emails_sent": emails_sent,
            "opens": opens,
            "clicks": clicks,
            "replies": replies,
            "conversions": conversions,
            "open_rate": round(open_rate, 3),
            "click_rate": round(click_rate, 3),
            "reply_rate": round(reply_rate, 3),
            "conversion_rate": round(conversion_rate, 3),
            "recommendations": recommendations,
        }

    except Exception as e:
        print(f"⚠ Dashboard error: {e}")
        return {
            "campaign_name": f"Campaign {campaign_id}",
            "total_leads": 0,
            "emails_sent": 0,
            "opens": 0,
            "clicks": 0,
            "replies": 0,
            "conversions": 0,
            "open_rate": 0,
            "click_rate": 0,
            "reply_rate": 0,
            "conversion_rate": 0,
            "recommendations": [],
            "error": str(e),
        }

app.include_router(router)