# outreach_engine/processors/outreach_sender.py

from __future__ import annotations

import asyncio
import os
import random
import secrets
import time
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from outreach_engine.core.proxy_rotator import get_next_proxy
from outreach_engine.processors.follow_up_manager import determine_next_step
from outreach_engine.core.lead_manager import get_lead
from outreach_engine.database.event_repository import store_event
from outreach_engine.database.supabase_client import supabase
from outreach_engine.core.performance_logger import timer
from outreach_engine.utils.logger import get_logger
from outreach_engine.core.templates import TEMPLATES
from outreach_engine.core.gmail_sender import send_via_gmail

logger = get_logger(__name__)

MIN_SEND_DELAY_SECONDS   = int(os.getenv("MIN_SEND_DELAY_SECONDS", "0"))
MAX_SEND_DELAY_SECONDS   = int(os.getenv("MAX_SEND_DELAY_SECONDS", "0"))
RESEND_COOLDOWN_HOURS    = 12
FOLLOWUP_DELAY_HOURS     = int(os.getenv("FOLLOWUP_DELAY_HOURS", "48"))
SENDER_NAME              = os.getenv("SENDER_NAME", "Your Name").strip()
REPLY_TO                 = os.getenv("REPLY_TO", "").strip() or None

_RAW_TRACKING_BASE = (
    os.getenv("PUBLIC_TRACKING_BASE_URL")
    or os.getenv("TRACKING_BASE_URL")
    or os.getenv("PIXEL_BASE_URL")
    or ""
).rstrip("/")

_PLACEHOLDER = "https://YOUR_PUBLIC_DOMAIN"

if not _RAW_TRACKING_BASE or _RAW_TRACKING_BASE == _PLACEHOLDER:
    raise RuntimeError(
        "\n\n❌ PUBLIC_TRACKING_BASE_URL is not set (or is still the placeholder).\n"
        "Open tracking will NEVER work without a real public URL.\n\n"
        "Add this to your .env:\n"
        "  PUBLIC_TRACKING_BASE_URL=https://your-real-domain.com\n\n"
        "If you are developing locally use an ngrok / Cloudflare tunnel:\n"
        "  ngrok http 8000\n"
        "  → PUBLIC_TRACKING_BASE_URL=https://xxxx.ngrok.io\n"
    )

PUBLIC_TRACKING_BASE_URL = _RAW_TRACKING_BASE
logger.info(f"✅ Tracking base URL: {PUBLIC_TRACKING_BASE_URL}")

CTA_DESTINATION_URL = os.getenv(
    "CTA_DESTINATION_URL", "https://your-landing-page.com"
).strip()


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def _normalize_text(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _lead_name(lead: Dict[str, Any]) -> str:
    first = (lead.get("first_name") or "").strip()
    last  = (lead.get("last_name") or "").strip()
    name  = " ".join(filter(None, [first, last])).strip()
    if name:
        return name
    return (lead.get("name") or lead.get("person_name") or "").strip()


def _passes_minimum_quality(lead: Dict[str, Any]) -> bool:
    return bool(lead.get("email") and lead.get("company") and _lead_name(lead))


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _cooldown_passed(lead: Dict[str, Any]) -> bool:
    last = _parse_dt(lead.get("last_email_sent"))
    if not last:
        return True
    now = datetime.now(last.tzinfo) if last.tzinfo else datetime.utcnow()
    return now - last > timedelta(hours=RESEND_COOLDOWN_HOURS)


def _set_lead_fields(email: str, campaign_id: int, data: Dict[str, Any]) -> None:
    try:
        supabase.table("outreach_leads") \
            .update(data) \
            .eq("email", email) \
            .eq("campaign_id", campaign_id) \
            .execute()
    except Exception as e:
        logger.warning(f"Lead update failed for {email}: {e}")


def _mark_processing(lead_email: str, campaign_id: int, step: int) -> None:
    _set_lead_fields(lead_email, campaign_id, {
        "status":        "processing",
        "followup_step": step,
        "last_updated":  datetime.utcnow().isoformat(),
    })


def _mark_sent(lead_email: str, campaign_id: int, step: int) -> None:
    now           = datetime.utcnow()
    next_followup = (now + timedelta(hours=FOLLOWUP_DELAY_HOURS)).isoformat()
    _set_lead_fields(lead_email, campaign_id, {
        "status":           "sent",
        "followup_step":    step,
        "last_email_sent":  now.isoformat(),
        "next_followup":    next_followup,
        "last_updated":     now.isoformat(),
    })


def _mark_failed(lead_email: str, campaign_id: int) -> None:
    _set_lead_fields(lead_email, campaign_id, {
        "status":       "failed",
        "last_updated": datetime.utcnow().isoformat(),
    })


def _should_send_followup(lead: Dict[str, Any], next_step: int) -> bool:
    status       = _normalize_text(lead.get("status"))
    current_step = int(lead.get("followup_step") or 0)
    if status != "sent":
        return False
    if next_step == -1:
        return False
    if next_step <= current_step:
        return False
    return True


def _choose_template_name(lead: Dict[str, Any], step: int) -> str:
    industry = _normalize_text(lead.get("industry"))

    if step == 0:
        if "saas" in industry:
            return "cold_email_saas"
        if any(x in industry for x in ("ecommerce", "commerce", "retail")):
            return "cold_email_ecommerce"
        return "cold_email"

    # FIX: Check BOTH open_count and followup_open_count
    open_count     = int(lead.get("open_count") or 0)
    followup_opens = int(lead.get("followup_open_count") or 0)
    reply_count    = int(lead.get("reply_count") or 0)

    if reply_count >= 1:
        return "followup_replied"
    if open_count >= 1 or followup_opens >= 1:
        return "followup_soft_open"
    return "followup_no_open"


def _safe_format(text: Optional[str], context: Dict[str, Any]) -> str:
    if not text:
        return ""
    return str(text).format_map(_SafeDict(context))


def _build_tracking_urls(
    lead_id:     int,
    campaign_id: int,
    email_type:  str = "cold",
) -> Dict[str, str]:
    ts    = int(time.time())
    token = secrets.token_hex(8)

    pixel_url = (
        f"{PUBLIC_TRACKING_BASE_URL}/open/{lead_id}"
        f"?campaign_id={campaign_id}"
        f"&email_type={email_type}"
        f"&ts={ts}"
        f"&t={token}"
    )
    click_url = (
        f"{PUBLIC_TRACKING_BASE_URL}/click/{lead_id}"
        f"?campaign_id={campaign_id}"
        f"&url={quote(CTA_DESTINATION_URL, safe='')}"
    )
    return {"cta_url": click_url, "pixel_url": pixel_url}


def _render_template(template_name: str, context: Dict[str, Any]) -> Dict[str, str]:
    template = TEMPLATES.get(template_name) or TEMPLATES["cold_email"]
    return {
        "subject":   _safe_format(template.get("subject"), context),
        "body":      _safe_format(template.get("body"), context),
        "html_body": _safe_format(template.get("html_body"), context),
    }


def _build_pixel_tag(pixel_url: str) -> str:
    return (
        f'<img src="{pixel_url}" '
        f'width="1" height="1" '
        f'style="display:none;opacity:0;position:absolute;" alt="" />'
    )


def _build_email_payload(
    lead:        Dict[str, Any],
    campaign_id: int,
    step:        int,
) -> Dict[str, str]:
    lead_id       = lead.get("id")
    sender_name   = lead.get("sender_name") or SENDER_NAME
    template_name = _choose_template_name(lead, step)
    email_type    = "cold" if step == 0 else "followup"
    tracking      = _build_tracking_urls(lead_id, campaign_id, email_type)
    pixel_tag     = _build_pixel_tag(tracking["pixel_url"])

    context = {
        **lead,
        "lead_id":       lead_id,
        "campaign_id":   campaign_id,
        "sender_name":   sender_name,
        "cta_text":      "Click here to learn more.",
        "cta_url":       tracking["cta_url"],
        "pixel_tag":     pixel_tag,
        "dynamic_offer": lead.get("dynamic_offer") or "our automated outreach system",
        "pain_hook":     lead.get("pain_hook") or "low reply rates",
        "name":          _lead_name(lead) or "there",
        "company":       lead.get("company") or "",
    }

    rendered  = _render_template(template_name, context)
    body      = rendered["body"]
    html_body = rendered["html_body"]

    if html_body and tracking["pixel_url"] not in html_body:
        if "</body>" in html_body:
            html_body = html_body.replace("</body>", f"  {pixel_tag}\n</body>")
        else:
            html_body = html_body + pixel_tag
        logger.debug(f"📌 Pixel injected as fallback for template '{template_name}' lead={lead_id}")

    for bad in (
        "http://localhost", "https://localhost",
        "http://127.0.0.1", "https://127.0.0.1",
    ):
        body      = body.replace(bad, "")
        html_body = html_body.replace(bad, "")

    return {
        "subject":    rendered["subject"],
        "body":       body,
        "html_body":  html_body,
        "lead_id":    lead_id,
        "email_type": email_type,
        "pixel_url":  tracking["pixel_url"],
    }


@timer("send_times")
def send_email_sync(
    lead_email:       str,
    campaign_id:      int,
    initial_outreach: bool = False,
    test_mode_active: Optional[bool] = None,
) -> bool:
    lead = get_lead(lead_email, campaign_id)
    if not lead:
        return False

    lead_id = lead.get("id")
    status  = _normalize_text(lead.get("status"))

    if status in {"processing", "replied", "converted", "opt-out", "failed"}:
        return False

    if not _passes_minimum_quality(lead):
        return False

    last_email_sent = lead.get("last_email_sent")
    is_cold_lead    = (
        status in {"new", "pending", "not_contacted", ""}
        and not last_email_sent
    )

    if is_cold_lead:
        if not _cooldown_passed(lead):
            return False
        step     = 0
        can_send = True
    else:
        next_step = determine_next_step(lead_email, campaign_id)
        step      = next_step
        can_send  = _should_send_followup(lead, next_step)

    if not can_send:
        return False

    email = _build_email_payload(lead=lead, campaign_id=campaign_id, step=step)

    if not email["subject"] or not email["body"] or not email["html_body"]:
        return False

    if email["pixel_url"] not in email["html_body"]:
        logger.error(
            f"❌ PIXEL MISSING from html_body for lead={lead_id} "
            f"template step={step}. Open tracking will NOT work for this send."
        )

    _mark_processing(lead_email, campaign_id, step)

    try:
        proxy = get_next_proxy()
        if proxy:
            logger.info(f"Using proxy: {proxy}")

        result = send_via_gmail(
            to_email=lead_email,
            subject=email["subject"],
            body=email["body"],
            html_body=email["html_body"],
            tracking_pixel_url=None,
            reply_to=REPLY_TO,
        )

        if not result:
            raise RuntimeError("send_via_gmail returned no result")

    except Exception as e:
        _mark_failed(lead_email, campaign_id)
        store_event(
            lead_id=lead_id,
            campaign_id=campaign_id,
            event_type="failed",
            metadata={"error": str(e)},
        )
        logger.error(f"❌ Failed → {lead_email}: {e}")
        return False

    _mark_sent(lead_email, campaign_id, step)

    thread_id    = None
    gmail_msg_id = None
    if isinstance(result, dict):
        thread_id    = result.get("thread_id")
        gmail_msg_id = result.get("message_id")

    if thread_id or gmail_msg_id:
        try:
            extra: Dict[str, Any] = {"last_updated": datetime.utcnow().isoformat()}
            if thread_id:
                extra["thread_id"] = thread_id
            if gmail_msg_id:
                extra["gmail_message_id"] = gmail_msg_id
            _set_lead_fields(lead_email, campaign_id, extra)
        except Exception:
            pass

    if step > 0:
        template_name       = _choose_template_name(lead, step)
        followup_status_val = (
            "no_open"   if template_name == "followup_no_open"   else
            "soft_open" if template_name == "followup_soft_open" else
            None
        )
        if followup_status_val:
            _set_lead_fields(lead_email, campaign_id, {
                "followup_status":       followup_status_val,
                "last_followup_sent_at": datetime.utcnow().isoformat(),
            })

    store_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="sent",
        metadata={
            "provider":         "gmail",
            "step":             step,
            "email_type":       email["email_type"],
            "thread_id":        thread_id,
            "gmail_message_id": gmail_msg_id,
        },
    )

    logger.info(f"✅ Sent → {lead_email} (step {step}, type={email['email_type']})")
    return True


async def send_email_async(*args, **kwargs):
    loop = asyncio.get_running_loop()
    fn   = partial(send_email_sync, *args, **kwargs)
    return await loop.run_in_executor(None, fn)


async def send_bulk_emails(
    leads: List[dict], concurrency: int = 10, **kwargs
) -> List:
    filtered = []
    seen:    set = set()

    for l in leads:
        email       = l.get("email")
        campaign_id = l.get("campaign_id")
        if not email or campaign_id is None:
            continue

        key = (email.strip().lower(), int(campaign_id))
        if key in seen:
            continue

        db = get_lead(email, campaign_id)
        if not db:
            continue

        db_status = _normalize_text(db.get("status"))
        if db_status in {
            "sent", "processing", "replied",
            "converted", "failed", "opt-out",
        }:
            continue

        filtered.append(db)
        seen.add(key)

    sem = asyncio.Semaphore(concurrency)

    async def worker(l):
        async with sem:
            low  = min(MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS)
            high = max(MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS)
            if high > 0:
                await asyncio.sleep(random.randint(low, high))
            return await send_email_async(
                l["email"], l["campaign_id"], **kwargs
            )

    return list(
        await asyncio.gather(*[worker(x) for x in filtered], return_exceptions=True)
    )
