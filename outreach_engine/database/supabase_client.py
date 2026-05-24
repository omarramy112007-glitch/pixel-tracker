# outreach_engine/database/supabase_client.py
"""
Supabase Client — DB Gateway.

Responsibilities:
  - Initialize and expose the Supabase client
  - Provide typed helper wrappers for common DB operations
  - NO business logic — pure data access layer

Exposed helpers:
  get_lead(lead_id)            → Dict | None
  update_lead(lead_id, data)   → None
  insert_event(payload)        → None
  get_outreach_lead(id)        → Dict | None
  update_outreach_lead(id, data) → None
  fetch_ready_leads(min_score) → List[Dict]
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from dotenv import load_dotenv
from supabase import create_client, Client


# ---------------------------------------------------------------------------
# Client initialization
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "❌ Supabase credentials missing.\n"
        "Create a .env file with:\n"
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


# ---------------------------------------------------------------------------
# Scalar helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
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
    return str(value).strip().lower() or default


def _normalize_reply_status(value: Any) -> str:
    if value is None:
        return "no_reply"
    if isinstance(value, bool):
        return "Replied" if value else "no_reply"
    return str(value).strip() or "no_reply"


def _chunked(items: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for i in range(0, len(items), size):
        yield items[i: i + size]


# ---------------------------------------------------------------------------
# Helper wrappers — leads table
# ---------------------------------------------------------------------------

def get_lead(lead_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single lead by UUID id."""
    try:
        res = supabase.table("leads").select("*").eq("id", lead_id).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"⚠️ get_lead failed for id={lead_id}: {e}")
        return None


def update_lead(lead_id: str, data: Dict[str, Any]) -> None:
    """Update any fields on a lead row."""
    try:
        payload = {**data, "updated_at": _utc_now_iso()}
        supabase.table("leads").update(payload).eq("id", lead_id).execute()
    except Exception as e:
        print(f"⚠️ update_lead failed for id={lead_id}: {e}")


# ---------------------------------------------------------------------------
# Helper wrappers — outreach_leads table
# ---------------------------------------------------------------------------

def get_outreach_lead(lead_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single outreach lead by integer id."""
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"⚠️ get_outreach_lead failed for id={lead_id}: {e}")
        return None


def update_outreach_lead(lead_id: int, data: Dict[str, Any]) -> None:
    """Update any fields on an outreach lead row."""
    try:
        payload = {**data, "last_updated": _utc_now_iso()}
        supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
    except Exception as e:
        print(f"⚠️ update_outreach_lead failed for id={lead_id}: {e}")


# ---------------------------------------------------------------------------
# Helper wrappers — lead_events table
# ---------------------------------------------------------------------------

def insert_event(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Insert a single event row.
    Falls back to dropping campaign_id if schema rejects it.
    """
    try:
        res = supabase.table("lead_events").insert(payload).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        msg = str(e).lower()
        if "campaign_id" in msg or "schema cache" in msg or "does not exist" in msg:
            fallback = dict(payload)
            fallback.pop("campaign_id", None)
            try:
                res = supabase.table("lead_events").insert(fallback).execute()
                return res.data[0] if res.data else None
            except Exception as e2:
                print(f"⚠️ insert_event fallback failed: {e2}")
                return None
        print(f"⚠️ insert_event failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Named state helpers (thin wrappers — business logic lives in lead_manager)
# ---------------------------------------------------------------------------

def mark_contacted(lead_id: str) -> None:
    update_lead(lead_id, {
        "outreach_status": "contacted",
        "last_contacted": _utc_now_iso(),
        "pipeline_stage": "Contacted",
    })


def mark_replied(lead_id: str) -> None:
    res = supabase.table("leads").select("reply_count").eq("id", lead_id).limit(1).execute()
    current = _safe_int(res.data[0].get("reply_count") if res.data else 0)
    update_lead(lead_id, {
        "reply_status": "Replied",
        "reply_count": current + 1,
        "pipeline_stage": "Qualified",
        "last_contacted": _utc_now_iso(),
    })


def mark_interested(lead_id: str) -> None:
    update_lead(lead_id, {
        "reply_status": "Interested",
        "pipeline_stage": "Interested",
    })


def update_pipeline_stage(lead_id: str, stage: str) -> None:
    update_lead(lead_id, {"pipeline_stage": stage})


def book_meeting(lead_id: str) -> None:
    res = supabase.table("leads").select("meeting_count").eq("id", lead_id).limit(1).execute()
    current = _safe_int(res.data[0].get("meeting_count") if res.data else 0)
    update_lead(lead_id, {
        "meeting_booked": True,
        "meeting_count": current + 1,
        "pipeline_stage": "Proposal",
    })


def close_deal(lead_id: str, value: float) -> None:
    update_lead(lead_id, {
        "deal_status": "Won",
        "deal_value": _safe_float(value),
        "deal_closed": True,
        "pipeline_stage": "Closed",
    })


def lose_deal(lead_id: str) -> None:
    update_lead(lead_id, {
        "deal_status": "Lost",
        "pipeline_stage": "Closed",
    })


# ---------------------------------------------------------------------------
# Lead ingestion helpers
# ---------------------------------------------------------------------------

def _build_lead_payload(lead: Dict[str, Any]) -> Dict[str, Any]:
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
        "company_score": _safe_int(lead.get("company_score")),
        "automation_score": _safe_int(lead.get("automation_score")),
        "seniority_score": _safe_int(lead.get("seniority_score")),
        "person_score": _safe_int(lead.get("person_score")),
        "pain_score": _safe_int(lead.get("pain_score")),
        "email_risk_score": _safe_int(lead.get("email_risk_score")),
        "tech_stack": lead.get("tech_stack"),
        "pain_signals": lead.get("pain_signals"),
        "email_valid": _safe_bool(lead.get("email_valid")),
        "outreach_status": _normalize_status(lead.get("outreach_status"), "not_contacted"),
        "reply_status": _normalize_reply_status(lead.get("reply_status")),
        "deal_status": _normalize_status(lead.get("deal_status"), "open"),
        "pipeline_stage": lead.get("pipeline_stage") or "Prospect",
        "meeting_booked": _safe_bool(lead.get("meeting_booked")),
        "deal_value": _safe_float(lead.get("deal_value")),
        "open_count": _safe_int(lead.get("open_count")),
        "reply_count": _safe_int(lead.get("reply_count")),
        "meeting_count": _safe_int(lead.get("meeting_count")),
        "followup_count": _safe_int(lead.get("followup_count")),
        "deal_closed": _safe_bool(lead.get("deal_closed")),
        "email_opened": _safe_bool(lead.get("email_opened")),
        "created_at": lead.get("created_at") or now_iso,
        "updated_at": lead.get("updated_at") or now_iso,
    }
    for key in ("last_contacted", "email_sent_at", "email_opened_at", "last_followup_at", "replied_at"):
        if lead.get(key):
            payload[key] = lead[key]
    return payload


def insert_lead(lead: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    email = (lead.get("email") or "").strip()
    website = (lead.get("website") or "").strip()
    if not email or not website:
        return None
    try:
        res = supabase.table("leads").upsert(_build_lead_payload(lead), on_conflict="email,website").execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"❌ insert_lead error: {e}")
        return None


def insert_leads_bulk(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not leads:
        return []
    payloads = [
        _build_lead_payload(l)
        for l in leads
        if (l.get("email") or "").strip() and (l.get("website") or "").strip()
    ]
    if not payloads:
        return []
    results: List[Dict[str, Any]] = []
    try:
        for batch in _chunked(payloads, 200):
            res = supabase.table("leads").upsert(batch, on_conflict="email,website").execute()
            if res.data:
                results.extend(res.data)
        print(f"✅ Bulk inserted: {len(payloads)} leads")
    except Exception as e:
        print(f"❌ Bulk insert error: {e}")
    return results


# ---------------------------------------------------------------------------
# Ready leads (for scheduler)
# ---------------------------------------------------------------------------

WEEK_WINDOW_DAYS = int(os.getenv("READY_LEADS_WINDOW_DAYS", "7"))

READY_STATUSES = {"pending", "new", "not_contacted", "sent", "contacted", "rate_limited"}
CLOSED_STATUSES = {"replied", "failed", "converted", "unsubscribed", "opt-out", "completed", "interested"}


def _is_within_window(created_at: Any) -> bool:
    if not created_at:
        return False
    try:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= (_utc_now() - timedelta(days=WEEK_WINDOW_DAYS))
    except Exception:
        return False


def _lead_quality_score(lead: Dict[str, Any]) -> float:
    return (
        _safe_int(lead.get("open_count")) * 2
        + _safe_int(lead.get("click_count")) * 4
        + _safe_int(lead.get("reply_count")) * 10
        + _safe_int(lead.get("conversion_count")) * 25
    )


def fetch_ready_leads(min_score: float = 0.0) -> List[Dict[str, Any]]:
    """
    Return outreach leads that are eligible for follow-up:
      - not in a closed status
      - have email + company + campaign_id
      - within the recency window
      - quality score >= min_score
    """
    try:
        response = supabase.table("outreach_leads").select("*").execute()
        all_leads = response.data or []
        ready: List[Dict[str, Any]] = []

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
                ready.append(lead)

        ready.sort(key=lambda x: x.get("quality_score", 0), reverse=True)
        return ready

    except Exception as e:
        print(f"⚠️ fetch_ready_leads failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Test lead
# ---------------------------------------------------------------------------

async def fetch_test_lead() -> List[Dict[str, Any]]:
    await asyncio.sleep(0)
    now_iso = _utc_now_iso()
    return [{
        "id": 999999,
        "person_name": "Test Lead",
        "email": "test@mycompany.com",
        "company": "TestCo",
        "website": "https://testco.com",
        "industry": "test",
        "title": "Founder",
        "campaign_id": 1,
        "followup_step": 0,
        "status": "pending",
        "open_count": 0,
        "click_count": 0,
        "reply_count": 0,
        "conversion_count": 0,
        "metadata": {},
        "created_at": now_iso,
        "last_updated": now_iso,
        "quality_score": 0,
    }]
