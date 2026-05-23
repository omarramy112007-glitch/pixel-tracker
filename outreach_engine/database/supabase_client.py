# outreach_engine/database/supabase_client.py

import os
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Iterable

from dotenv import load_dotenv
from supabase import create_client, Client

# -----------------------------------------------------------------------------
# Environment / client init
# -----------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "❌ Supabase credentials missing.\n"
        "Create a .env file in the project root with:\n"
        "SUPABASE_URL=your_url\n"
        "SUPABASE_KEY=your_key"
    )

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialized")
except Exception as e:
    raise RuntimeError(f"❌ Failed to initialize Supabase client: {e}") from e


def get_supabase() -> Client:
    return supabase


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _normalize_status(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip().lower()
    return text or default


def _chunked(items: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# -----------------------------------------------------------------------------
# Leads table payload
# -----------------------------------------------------------------------------

def _build_payload(lead: Dict[str, Any]) -> Dict[str, Any]:
    now_iso = _utc_now_iso()

    payload = {
        "person_name": lead.get("person_name") or lead.get("name"),
        "title": lead.get("title"),
        "email": lead.get("email"),
        "phone": lead.get("phone"),
        "company": lead.get("company"),
        "website": lead.get("website"),
        "source": lead.get("source") or lead.get("lead_source"),
        "country": lead.get("country"),
        "industry": lead.get("industry"),
        "title_category": lead.get("title_category"),

        "company_score": _safe_int(lead.get("company_score"), 0),
        "automation_score": _safe_int(lead.get("automation_score"), 0),
        "seniority_score": _safe_int(lead.get("seniority_score"), 0),
        "person_score": _safe_int(lead.get("person_score"), 0),
        "pain_score": _safe_int(lead.get("pain_score"), 0),
        "email_risk_score": _safe_int(lead.get("email_risk_score"), 0),

        "tech_stack": lead.get("tech_stack"),
        "pain_signals": lead.get("pain_signals"),
        "email_valid": _safe_bool(lead.get("email_valid"), False),

        "outreach_status": _normalize_status(lead.get("outreach_status"), "not_contacted"),
        "reply_status": _safe_bool(lead.get("reply_status"), False),
        "deal_status": _normalize_status(lead.get("deal_status"), "open"),
        "pipeline_stage": lead.get("pipeline_stage") or "Prospect",

        "meeting_booked": _safe_bool(lead.get("meeting_booked"), False),
        "deal_value": _safe_float(lead.get("deal_value"), 0.0),

        "open_count": _safe_int(lead.get("open_count"), 0),
        "reply_count": _safe_int(lead.get("reply_count"), 0),
        "meeting_count": _safe_int(lead.get("meeting_count"), 0),
        "followup_count": _safe_int(lead.get("followup_count"), 0),

        "deal_closed": _safe_bool(lead.get("deal_closed"), False),
        "email_opened": _safe_bool(lead.get("email_opened"), False),

        "created_at": lead.get("created_at") or now_iso,
        "updated_at": lead.get("updated_at") or now_iso,
    }

    for key in (
        "last_contacted",
        "email_sent_at",
        "email_opened_at",
        "last_followup_at",
        "replied_at",
    ):
        if lead.get(key):
            payload[key] = lead.get(key)

    return payload


# -----------------------------------------------------------------------------
# Inserts / updates
# -----------------------------------------------------------------------------

def insert_lead(lead: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    email = (lead.get("email") or "").strip()
    website = (lead.get("website") or "").strip()

    if not email or not website:
        return None

    payload = _build_payload(lead)

    try:
        response = (
            supabase.table("leads")
            .upsert(payload, on_conflict="email,website")
            .execute()
        )
        return response.data[0] if response.data else None

    except Exception as e:
        print(f"❌ insert_lead error: {e}")
        return None


def insert_leads_bulk(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not leads:
        return []

    payloads = [
        _build_payload(l)
        for l in leads
        if (l.get("email") or "").strip() and (l.get("website") or "").strip()
    ]

    if not payloads:
        return []

    results: List[Dict[str, Any]] = []

    try:
        for batch in _chunked(payloads, 200):
            response = (
                supabase.table("leads")
                .upsert(batch, on_conflict="email,website")
                .execute()
            )
            if response.data:
                results.extend(response.data)

        print(f"✅ Bulk inserted: {len(payloads)} leads")
        return results

    except Exception as e:
        print(f"❌ Bulk insert error: {e}")
        return results


def update_pipeline_stage(lead_id: str, stage: str):
    try:
        supabase.table("leads").update({
            "pipeline_stage": stage,
            "updated_at": _utc_now_iso(),
        }).eq("id", lead_id).execute()
    except Exception as e:
        print(f"❌ update_pipeline_stage error: {e}")


def mark_contacted(lead_id: str):
    try:
        supabase.table("leads").update({
            "outreach_status": "contacted",
            "last_contacted": _utc_now_iso(),
            "pipeline_stage": "Contacted",
            "updated_at": _utc_now_iso(),
        }).eq("id", lead_id).execute()
    except Exception as e:
        print(f"❌ mark_contacted error: {e}")


def mark_replied(lead_id: str):
    try:
        existing = (
            supabase.table("leads")
            .select("reply_count")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        current = 0
        if existing.data:
            current = _safe_int(existing.data[0].get("reply_count"), 0)

        supabase.table("leads").update({
            "reply_status": True,
            "reply_count": current + 1,
            "pipeline_stage": "Qualified",
            "last_contacted": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        }).eq("id", lead_id).execute()

    except Exception as e:
        print(f"❌ mark_replied error: {e}")


def book_meeting(lead_id: str):
    try:
        existing = (
            supabase.table("leads")
            .select("meeting_count")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )

        current = 0
        if existing.data:
            current = _safe_int(existing.data[0].get("meeting_count"), 0)

        supabase.table("leads").update({
            "meeting_booked": True,
            "meeting_count": current + 1,
            "pipeline_stage": "Proposal",
            "updated_at": _utc_now_iso(),
        }).eq("id", lead_id).execute()

    except Exception as e:
        print(f"❌ book_meeting error: {e}")


def close_deal(lead_id: str, value: float):
    try:
        supabase.table("leads").update({
            "deal_status": "won",
            "deal_value": _safe_float(value, 0.0),
            "deal_closed": True,
            "pipeline_stage": "Closed",
            "updated_at": _utc_now_iso(),
        }).eq("id", lead_id).execute()
    except Exception as e:
        print(f"❌ close_deal error: {e}")


def lose_deal(lead_id: str):
    try:
        supabase.table("leads").update({
            "deal_status": "lost",
            "pipeline_stage": "Closed",
            "updated_at": _utc_now_iso(),
        }).eq("id", lead_id).execute()
    except Exception as e:
        print(f"❌ lose_deal error: {e}")


# -----------------------------------------------------------------------------
# Outreach leads readiness
# -----------------------------------------------------------------------------

WEEK_WINDOW_DAYS = int(os.getenv("READY_LEADS_WINDOW_DAYS", "7"))

READY_STATUSES = {"pending", "new", "not_contacted", "sent", "contacted"}
CLOSED_STATUSES = {"replied", "failed", "converted", "unsubscribed", "opt-out", "completed"}


def _is_within_window(created_at: Any) -> bool:
    if not created_at:
        return False

    try:
        created_time = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if created_time.tzinfo is None:
            created_time = created_time.replace(tzinfo=timezone.utc)

        return created_time >= (_utc_now() - timedelta(days=WEEK_WINDOW_DAYS))
    except Exception:
        return False


def _lead_quality_score(lead: Dict[str, Any]) -> float:
    open_count = _safe_int(lead.get("open_count"), 0)
    click_count = _safe_int(lead.get("click_count"), 0)
    reply_count = _safe_int(lead.get("reply_count"), 0)
    conversion_count = _safe_int(lead.get("conversion_count"), 0)

    return (
        open_count * 2
        + click_count * 4
        + reply_count * 10
        + conversion_count * 25
    )


def fetch_ready_leads(min_score: float = 0.0) -> List[Dict[str, Any]]:
    """
    Returns outreach_leads that are:
    - not closed
    - have email + company + campaign_id
    - are within the configurable recency window
    - score >= min_score
    """
    try:
        response = supabase.table("outreach_leads").select("*").execute()
        all_leads = response.data or []

        ready_leads: List[Dict[str, Any]] = []

        for lead in all_leads:
            email = (lead.get("email") or "").strip()
            company = (lead.get("company") or "").strip()
            campaign_id = lead.get("campaign_id")
            status = (lead.get("status") or "pending").strip().lower()

            if not email or not company or not campaign_id:
                continue

            if status in CLOSED_STATUSES:
                continue

            if status not in READY_STATUSES:
                continue

            if WEEK_WINDOW_DAYS > 0 and not _is_within_window(lead.get("created_at")):
                continue

            score = _lead_quality_score(lead)
            lead["quality_score"] = score

            if score >= float(min_score):
                ready_leads.append(lead)

        ready_leads.sort(key=lambda x: x.get("quality_score", 0), reverse=True)
        return ready_leads

    except Exception as e:
        print(f"⚠ Failed to fetch ready leads: {e}")
        return []


# -----------------------------------------------------------------------------
# Test lead
# -----------------------------------------------------------------------------

async def fetch_test_lead() -> List[Dict[str, Any]]:
    await asyncio.sleep(0)
    now_iso = _utc_now_iso()

    return [{
        "id": 999999,
        "person_name": "Test Lead",
        "email": "test@mycompany.com",
        "phone": None,
        "company": "TestCo",
        "website": "https://testco.com",
        "industry": "test",
        "title": "Founder",
        "title_category": "founder",
        "source": "test",
        "lead_source": "test",
        "campaign_id": 1,
        "followup_step": 0,
        "last_email_sent": None,
        "next_followup": None,
        "status": "pending",
        "open_count": 0,
        "click_count": 0,
        "reply_count": 0,
        "conversion_count": 0,
        "metadata": {},
        "created_at": now_iso,
        "last_updated": now_iso,
        "quality_score": 0,
        "reply_status": False,
        "email_opened": False,
        "deal_closed": False,
    }]
