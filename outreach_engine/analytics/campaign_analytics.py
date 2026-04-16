# File: outreach_engine/analytics/campaign_analytics.py

from __future__ import annotations

from typing import Any, Dict, Optional

from outreach_engine.tracking.event_repository import (
    log_event,
    get_campaign_metrics as _get_campaign_metrics,
    get_campaign_funnel as _get_campaign_funnel,
)


def _merge_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base: Dict[str, Any] = {"channel": "email"}
    if isinstance(metadata, dict):
        base.update(metadata)
    return base


# --------------------------------------------------
# EVENT WRAPPERS (thin layer only)
# --------------------------------------------------
def record_email_sent(campaign_id: int, lead_id: Optional[int] = None, metadata=None):
    if not lead_id:
        return
    return log_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="sent",
        metadata=_merge_metadata(metadata),
    )


def record_open(campaign_id: int, lead_id: Optional[int] = None, metadata=None):
    if not lead_id:
        return
    return log_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="opened",
        metadata=_merge_metadata(metadata),
    )


def record_click(campaign_id: int, lead_id: Optional[int] = None, metadata=None):
    if not lead_id:
        return
    return log_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="clicked",
        metadata=_merge_metadata(metadata),
    )


def record_reply(campaign_id: int, lead_id: Optional[int] = None, metadata=None):
    if not lead_id:
        return
    return log_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="replied",
        metadata=_merge_metadata(metadata),
    )


def record_conversion(campaign_id: int, lead_id: Optional[int] = None, metadata=None):
    if not lead_id:
        return
    return log_event(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="converted",
        metadata=_merge_metadata(metadata),
    )


# --------------------------------------------------
# READ WRAPPERS
# --------------------------------------------------
def get_real_time_metrics(campaign_id: int) -> Dict[str, Any]:
    return _get_campaign_metrics(campaign_id)


def get_campaign_metrics(campaign_id: int) -> Dict[str, Any]:
    return _get_campaign_metrics(campaign_id)


def get_campaign_funnel(campaign_id: int) -> Dict[str, Any]:
    return _get_campaign_funnel(campaign_id)