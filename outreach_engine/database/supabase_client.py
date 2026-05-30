# outreach_engine/database/supabase_client.py
"""
Supabase Client — DB Gateway.

Responsibilities:
  - Initialize and expose the Supabase client
  - Provide thin helper wrappers for common DB operations
  - NO business logic — pure data access layer
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
    text = str(value).strip().lower()
    return text or default


def _chunked(
    items: List[Dict[str, Any]], size: int
) -> Iterable[List[Dict[str, Any]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _merge_metadata(
    existing: Any, patch: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    base = existing if isinstance(existing, dict) else {}
    return {**base, **(patch or {})}


# ---------------------------------------------------------------------------
# Leads table helpers
# ---------------------------------------------------------------------------

def get_lead(lead_id: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            supabase.table("leads")
            .select("*")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"⚠️ get_lead failed for id={lead_id}: {e}")
        return None


def get_lead_by_email(email: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            supabase.table("leads")
            .select("*")
            .ilike("email", email)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"⚠️ get_lead_by_email failed for email={email}: {e}")
        return None


def update_lead(lead_id: str, data: Dict[str, Any]) -> None:
    try:
        payload = {**data, "updated_at": _utc_now_iso()}
        supabase.table("leads").update(payload).eq("id", lead_id).execute()
    except Exception as e:
        print(f"⚠️ update_lead failed for id={lead_id}: {e}")


# ---------------------------------------------------------------------------
# Outreach leads helpers
# ---------------------------------------------------------------------------

def _get_outreach_row(
    lead_id: Optional[int] = None,
    email: Optional[str] = None,
    campaign_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    try:
        query = supabase.table("outreach_leads").select("*")
        if lead_id is not None:
            query = query.eq("id", lead_id)
        elif email:
            query = query.ilike("email", email)
        if campaign_id is not None:
            query = query.eq("campaign_id", campaign_id)
        res = query.limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"⚠️ outreach lead lookup failed: {e}")
        return None


def get_outreach_lead(lead_id: int) -> Optional[Dict[str, Any]]:
    return _get_outreach_row(lead_id=lead_id)


def get_outreach_lead_by_email_campaign(
    email: str,
    campaign_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    return _get_outreach_row(email=email, campaign_id=campaign_id)


def get_outreach_lead_by_email(email: str) -> Optional[Dict[str, Any]]:
    return _get_outreach_row(email=email)


def update_outreach_lead(lead_id: int, data: Dict[str, Any]) -> None:
    try:
        payload = {**data, "last_updated": _utc_now_iso()}
        supabase.table("outreach_leads").update(payload).eq("id", lead_id).execute()
    except Exception as e:
        print(f"⚠️ update_outreach_lead failed for id={lead_id}: {e}")


def update_outreach_lead_by_email_campaign(
    email: str,
    campaign_id: Optional[int],
    data: Dict[str, Any],
) -> None:
    try:
        query = supabase.table("outreach_leads").update(
            {**data, "last_updated": _utc_now_iso()}
        ).ilike("email", email)
        if campaign_id is not None:
            query = query.eq("campaign_id", campaign_id)
        query.execute()
    except Exception as e:
        print(f"⚠️ update_outreach_lead_by_email_campaign failed for {email}: {e}")


def get_campaign_leads(campaign_id: int) -> List[Dict[str, Any]]:
    try:
        res = (
            supabase.table("outreach_leads")
            .select("*")
            .eq("campaign_id", campaign_id)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"⚠️ get_campaign_leads failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Event storage
# ---------------------------------------------------------------------------

def insert_event(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        clean_payload = dict(payload)
        if "campaign_id" in clean_payload:
            clean_payload["metadata"] = _merge_metadata(
                clean_payload.get("metadata"),
                {"campaign_id": clean_payload.pop("campaign_id")},
            )
        res = supabase.table("lead_events").insert(clean_payload).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"⚠️ insert_event failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Lead ingestion helpers
# ---------------------------------------------------------------------------

def _build_lead_payload(lead: Dict[str, Any]) -> Dict[str, Any]:
    now_iso = _utc_now_iso()
    return {
        "person_name":      lead.get("person_name") or lead.get("name"),
        "title":            lead.get("title"),
        "email":            lead.get("email"),
        "phone":            lead.get("phone"),
        "company":          lead.get("company"),
        "website":          lead.get("website"),
        "source":           lead.get("source") or lead.get("lead_source"),
        "country":          lead.get("country"),
        "industry":         lead.get("industry"),
        "title_category":   lead.get("title_category"),
        "company_score":    _safe_int(lead.get("company_score")),
        "automation_score": _safe_int(lead.get("automation_score")),
        "seniority_score":  _safe_int(lead.get("seniority_score")),
        "person_score":     _safe_int(lead.get("person_score")),
        "pain_score":       _safe_int(lead.get("pain_score")),
        "email_risk_score": _safe_int(lead.get("email_risk_score")),
        "tech_stack":       lead.get("tech_stack"),
        "pain_signals":     lead.get("pain_signals"),
        "email_valid":      _safe_bool(lead.get("email_valid")),
        "outreach_status":  _normalize_status(lead.get("outreach_status"), "not_contacted"),
        "reply_status":     _safe_bool(lead.get("reply_status")),
        "deal_status":      _normalize_status(lead.get("deal_status"), "open"),
        "pipeline_stage":   lead.get("pipeline_stage") or "Prospect",
        "meeting_booked":   _safe_bool(lead.get("meeting_booked")),
        "deal_value":       _safe_float(lead.get("deal_value")),
        "open_count":       _safe_int(lead.get("open_count")),
        "reply_count":      _safe_int(lead.get("reply_count")),
        "meeting_count":    _safe_int(lead.get("meeting_count")),
        "followup_count":   _safe_int(lead.get("followup_count")),
        "deal_closed":      _safe_bool(lead.get("deal_closed")),
        "email_opened":     _safe_bool(lead.get("email_opened")),
        "created_at":       lead.get("created_at") or now_iso,
        "updated_at":       lead.get("updated_at") or now_iso,
    }


def insert_lead(lead: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    email   = (lead.get("email") or "").strip()
    website = (lead.get("website") or "").strip()
    if not email or not website:
        return None
    try:
        res = (
            supabase.table("leads")
            .upsert(_build_lead_payload(lead), on_conflict="email,website")
            .execute()
        )
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
            res = (
                supabase.table("leads")
                .upsert(batch, on_conflict="email,website")
                .execute()
            )
            if res.data:
                results.extend(res.data)
        print(f"✅ Bulk inserted: {len(payloads)} leads")
    except Exception as e:
        print(f"❌ Bulk insert error: {e}")
    return results


def _build_outreach_payload(lead: Dict[str, Any]) -> Dict[str, Any]:
    now_iso = _utc_now_iso()
    return {
        "email":              lead.get("email"),
        "first_name":         (
            lead.get("first_name")
            or (lead.get("name", "").split(" ")[0] if lead.get("name") else None)
        ),
        "last_name":          lead.get("last_name"),
        "company":            lead.get("company"),
        "industry":           lead.get("industry"),
        "lead_source":        lead.get("lead_source") or lead.get("source"),
        "campaign_id":        lead.get("campaign_id") or 1,
        "followup_step":      _safe_int(lead.get("followup_step")),
        "last_email_sent":    lead.get("last_email_sent"),
        "next_followup":      lead.get("next_followup"),
        "status":             _normalize_status(lead.get("status"), "pending"),
        "open_count":         _safe_int(lead.get("open_count")),
        "click_count":        _safe_int(lead.get("click_count")),
        "reply_count":        _safe_int(lead.get("reply_count")),
        "conversion_count":   _safe_int(lead.get("conversion_count")),
        "metadata":           lead.get("metadata") or {},
        "created_at":         lead.get("created_at") or now_iso,
        "last_updated":       lead.get("last_updated") or now_iso,
        "country":            lead.get("country"),
        "tech_stack":         lead.get("tech_stack"),
        "pain_points":        lead.get("pain_points"),
        "automation_maturity":lead.get("automation_maturity"),
        "score":              lead.get("score"),
        "last_contacted":     lead.get("last_contacted"),
        "replied_at":         lead.get("replied_at"),
        "thread_id":          lead.get("thread_id"),
        "gmail_message_id":   lead.get("gmail_message_id"),
        "email_opened":       _safe_bool(lead.get("email_opened")),
        "email_opened_at":    lead.get("email_opened_at"),
        "reply_status":       _safe_bool(lead.get("reply_status")),
        "link_clicked":       _safe_bool(lead.get("link_clicked")),
    }


def upsert_outreach_lead(lead: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    email = (lead.get("email") or "").strip()
    if not email:
        return None
    try:
        res = (
            supabase.table("outreach_leads")
            .upsert(_build_outreach_payload(lead), on_conflict="email,campaign_id")
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"❌ upsert_outreach_lead error: {e}")
        return None


def bulk_upsert_outreach_leads(
    leads: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not leads:
        return []
    payloads = [
        _build_outreach_payload(l)
        for l in leads
        if (l.get("email") or "").strip()
    ]
    if not payloads:
        return []
    results: List[Dict[str, Any]] = []
    try:
        for batch in _chunked(payloads, 200):
            res = (
                supabase.table("outreach_leads")
                .upsert(batch, on_conflict="email,campaign_id")
                .execute()
            )
            if res.data:
                results.extend(res.data)
        print(f"✅ Outreach bulk upserted: {len(payloads)} leads")
    except Exception as e:
        print(f"❌ bulk_upsert_outreach_leads error: {e}")
    return results


# ---------------------------------------------------------------------------
# Outreach state helpers
# ---------------------------------------------------------------------------

def _update_outreach_row(
    row: Dict[str, Any],
    updates: Dict[str, Any],
) -> None:
    if not row:
        return
    payload = dict(updates)
    payload["last_updated"] = _utc_now_iso()
    try:
        supabase.table("outreach_leads") \
            .update(payload) \
            .eq("id", row["id"]) \
            .eq("campaign_id", row["campaign_id"]) \
            .execute()
    except Exception as e:
        print(f"⚠️ outreach update failed: {e}")


def _update_system_lead_by_email(
    email: Optional[str], updates: Dict[str, Any]
) -> None:
    if not email:
        return
    try:
        supabase.table("leads") \
            .update({**updates, "updated_at": _utc_now_iso()}) \
            .ilike("email", email) \
            .execute()
    except Exception as e:
        print(f"⚠️ system lead update failed for {email}: {e}")


def record_email_sent(
    lead_id: int,
    campaign_id: int,
    *,
    email: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    row = _get_outreach_row(lead_id=lead_id, campaign_id=campaign_id) or (
        _get_outreach_row(email=email, campaign_id=campaign_id) if email else None
    )
    if not row:
        return

    now   = _utc_now_iso()
    patch = {
        "status":          "sent",
        "last_email_sent": now,
        "last_contacted":  now,
        "next_followup":   None,
        "metadata":        _merge_metadata(row.get("metadata"), metadata),
    }
    if row.get("followup_step") is None:
        patch["followup_step"] = 0

    _update_outreach_row(row, patch)

    if email:
        _update_system_lead_by_email(email, {
            "pipeline_stage": "Sent",
            "email_sent_at":  now,
        })


def record_open(
    lead_id: int,
    campaign_id: int,
    *,
    email: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    ── FIX: record_open no longer increments open_count ─────────────────────
    open_count is exclusively managed by pixel_server._track_open_db()
    and engagement_tracking._update_outreach_lead_counters().

    Calling record_open AND pixel_server in the same request path caused
    every open to be counted twice.  This function now only records
    the metadata / email_opened flag — it does NOT touch open_count.
    ─────────────────────────────────────────────────────────────────────────
    """
    row = _get_outreach_row(lead_id=lead_id, campaign_id=campaign_id) or (
        _get_outreach_row(email=email, campaign_id=campaign_id) if email else None
    )
    if not row:
        return

    now   = _utc_now_iso()
    patch = {
        # open_count intentionally NOT incremented here —
        # pixel_server._track_open_db is the sole owner
        "email_opened":    True,
        "email_opened_at": (
            metadata.get("timestamp")
            if metadata and metadata.get("timestamp")
            else now
        ),
        "metadata": _merge_metadata(row.get("metadata"), metadata),
    }
    _update_outreach_row(row, patch)

    if email:
        _update_system_lead_by_email(email, {
            "email_opened":    True,
            "email_opened_at": now,
        })


def record_click(
    lead_id: int,
    campaign_id: int,
    *,
    email: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    row = _get_outreach_row(lead_id=lead_id, campaign_id=campaign_id) or (
        _get_outreach_row(email=email, campaign_id=campaign_id) if email else None
    )
    if not row:
        return

    now   = _utc_now_iso()
    patch = {
        "click_count":  _safe_int(row.get("click_count")) + 1,
        "link_clicked": True,
        "metadata":     _merge_metadata(row.get("metadata"), metadata),
    }
    _update_outreach_row(row, patch)

    if email:
        _update_system_lead_by_email(email, {
            "link_clicked":    True,
            "link_clicked_at": now,
        })


def record_reply(
    lead_id: int,
    campaign_id: int,
    *,
    email: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    ── FIX: record_reply no longer increments reply_count ───────────────────
    reply_count is EXCLUSIVELY managed by:
      gmail_watcher._increment_reply_count_and_finalize()

    The previous implementation incremented reply_count here AND in
    _increment_reply_count_and_finalize, producing a count of 2 for
    every single reply.

    This function now only records metadata / thread tracking fields
    and sets reply_status=True. It does NOT touch reply_count or status.
    ─────────────────────────────────────────────────────────────────────────
    """
    row = _get_outreach_row(lead_id=lead_id, campaign_id=campaign_id) or (
        _get_outreach_row(email=email, campaign_id=campaign_id) if email else None
    )
    if not row:
        return

    now   = _utc_now_iso()
    patch = {
        # reply_count intentionally NOT incremented here —
        # gmail_watcher._increment_reply_count_and_finalize is the sole owner
        "reply_status": True,
        "replied_at":   now,
        "last_contacted": now,
        "metadata":     _merge_metadata(row.get("metadata"), metadata),
    }

    # Store thread/message ids for future lookups
    if metadata:
        if metadata.get("thread_id"):
            patch["thread_id"] = metadata["thread_id"]
        if metadata.get("gmail_message_id"):
            patch["gmail_message_id"] = metadata["gmail_message_id"]

    _update_outreach_row(row, patch)

    if email:
        _update_system_lead_by_email(email, {
            "reply_status":  True,
            "replied_at":    now,
            "last_contacted": now,
            "pipeline_stage": "Replied",
        })


def mark_followup_sent(
    lead_id: int,
    campaign_id: int,
    followup_type: str,
    *,
    email: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    row = _get_outreach_row(lead_id=lead_id, campaign_id=campaign_id) or (
        _get_outreach_row(email=email, campaign_id=campaign_id) if email else None
    )
    if not row:
        return

    now   = _utc_now_iso()
    patch = {
        "status":          followup_type,
        "followup_step":   _safe_int(row.get("followup_step")) + 1,
        "last_email_sent": now,
        "last_contacted":  now,
        "next_followup":   None,
        "metadata":        _merge_metadata(row.get("metadata"), metadata),
    }
    _update_outreach_row(row, patch)

    if email:
        stage_map = {
            "followup_no_open":   "Follow-up No Open",
            "followup_soft_open": "Follow-up Soft Open",
            "interested_followup":"Interested Follow-up",
        }
        _update_system_lead_by_email(email, {
            "pipeline_stage": stage_map.get(followup_type, followup_type),
            "last_contacted": now,
        })


def record_conversion(
    lead_id: int,
    campaign_id: int,
    *,
    email: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    row = _get_outreach_row(lead_id=lead_id, campaign_id=campaign_id) or (
        _get_outreach_row(email=email, campaign_id=campaign_id) if email else None
    )
    if not row:
        return

    now   = _utc_now_iso()
    patch = {
        "conversion_count": _safe_int(row.get("conversion_count")) + 1,
        "status":           "converted",
        "metadata":         _merge_metadata(row.get("metadata"), metadata),
        "last_updated":     now,
    }
    _update_outreach_row(row, patch)

    if email:
        _update_system_lead_by_email(email, {
            "deal_status":    "Won",
            "deal_closed":    True,
            "pipeline_stage": "Closed",
            "deal_value":     _safe_float((metadata or {}).get("deal_value"), 0.0),
        })


# ---------------------------------------------------------------------------
# Backwards-compatible helpers
# ---------------------------------------------------------------------------

def mark_contacted(lead_id: str) -> None:
    update_lead(lead_id, {
        "outreach_status": "contacted",
        "last_contacted":  _utc_now_iso(),
        "pipeline_stage":  "Contacted",
    })


def mark_replied(lead_id: str) -> None:
    """
    Compatibility helper — does NOT change outreach_leads status
    and does NOT increment reply_count (owned by gmail_watcher).
    """
    update_lead(lead_id, {
        "reply_status":   True,
        "pipeline_stage": "Replied",
        "last_contacted": _utc_now_iso(),
    })


def mark_interested(lead_id: str) -> None:
    update_lead(lead_id, {
        "reply_status":   True,
        "pipeline_stage": "Interested",
    })


def update_pipeline_stage(lead_id: str, stage: str) -> None:
    update_lead(lead_id, {"pipeline_stage": stage})


def book_meeting(lead_id: str) -> None:
    row = get_lead(lead_id) or {}
    update_lead(lead_id, {
        "meeting_booked": True,
        "meeting_count":  _safe_int(row.get("meeting_count")) + 1,
        "pipeline_stage": "Proposal",
    })


def close_deal(lead_id: str, value: float) -> None:
    update_lead(lead_id, {
        "deal_status":    "Won",
        "deal_value":     _safe_float(value),
        "deal_closed":    True,
        "pipeline_stage": "Closed",
    })


def lose_deal(lead_id: str) -> None:
    update_lead(lead_id, {
        "deal_status":    "Lost",
        "pipeline_stage": "Closed",
    })


# ---------------------------------------------------------------------------
# Ready leads / follow-up candidates
# ---------------------------------------------------------------------------

WEEK_WINDOW_DAYS = int(os.getenv("READY_LEADS_WINDOW_DAYS", "7"))

READY_STATUSES  = {"pending", "new", "not_contacted"}
CLOSED_STATUSES = {
    "replied", "failed", "converted", "unsubscribed",
    "opt-out", "completed", "lost", "closed",
}


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
        _safe_int(lead.get("open_count"))       * 2
        + _safe_int(lead.get("click_count"))    * 4
        + _safe_int(lead.get("reply_count"))    * 10
        + _safe_int(lead.get("conversion_count")) * 25
        + _safe_float(lead.get("score"))
    )


def fetch_ready_leads(
    min_score: float = 0.0, limit: int = 500
) -> List[Dict[str, Any]]:
    try:
        response  = supabase.table("outreach_leads").select("*").limit(limit).execute()
        all_leads = response.data or []
        ready: List[Dict[str, Any]] = []

        for lead in all_leads:
            email   = (lead.get("email")   or "").strip()
            company = (lead.get("company") or "").strip()
            status  = str(lead.get("status") or "pending").strip().lower()

            if not email or not company:
                continue
            if status in CLOSED_STATUSES:
                continue
            if status not in READY_STATUSES:
                continue
            if WEEK_WINDOW_DAYS > 0 and not _is_within_window(lead.get("created_at")):
                continue

            score             = _lead_quality_score(lead)
            lead["quality_score"] = score

            if score >= float(min_score):
                ready.append(lead)

        ready.sort(key=lambda x: x.get("quality_score", 0), reverse=True)
        return ready

    except Exception as e:
        print(f"⚠️ fetch_ready_leads failed: {e}")
        return []


def fetch_followup_candidates(
    mode: str,
    campaign_id: Optional[int] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    try:
        response  = supabase.table("outreach_leads").select("*").limit(limit).execute()
        all_leads = response.data or []
        picked: List[Dict[str, Any]] = []

        for lead in all_leads:
            if campaign_id is not None and _safe_int(
                lead.get("campaign_id")
            ) != _safe_int(campaign_id):
                continue

            status      = str(lead.get("status") or "").strip().lower()
            open_count  = _safe_int(lead.get("open_count"))
            reply_count = _safe_int(lead.get("reply_count"))

            if status != "sent":
                continue

            if mode == "followup_no_open":
                if open_count == 0 and reply_count == 0:
                    picked.append(lead)
            elif mode == "followup_soft_open":
                if open_count > 0 and reply_count == 0:
                    picked.append(lead)
            elif mode == "interested_followup":
                if open_count > 0 and reply_count > 0:
                    picked.append(lead)

        picked.sort(
            key=lambda x: (
                -_safe_float(x.get("score")),
                str(x.get("last_updated") or x.get("created_at") or ""),
            )
        )
        return picked

    except Exception as e:
        print(f"⚠️ fetch_followup_candidates failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Test lead helper
# ---------------------------------------------------------------------------

async def fetch_test_lead() -> List[Dict[str, Any]]:
    await asyncio.sleep(0)
    now_iso = _utc_now_iso()
    return [{
        "id":               999999,
        "person_name":      "Test Lead",
        "email":            "test@mycompany.com",
        "company":          "TestCo",
        "website":          "https://testco.com",
        "industry":         "test",
        "title":            "Founder",
        "campaign_id":      1,
        "followup_step":    0,
        "status":           "pending",
        "open_count":       0,
        "click_count":      0,
        "reply_count":      0,
        "conversion_count": 0,
        "metadata":         {},
        "created_at":       now_iso,
        "last_updated":     now_iso,
        "quality_score":    0,
    }]
