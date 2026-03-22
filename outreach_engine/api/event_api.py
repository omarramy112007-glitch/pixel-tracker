# outreach_engine/api/event_api.py

from fastapi import APIRouter
from tracking.event_repository import track_event, get_events

router = APIRouter()


@router.post("/event")
def create_event(event: dict):
    track_event(
        event_type=event["type"],
        lead_id=event["lead_id"],
        campaign_id=event["campaign_id"],
        metadata=event.get("metadata", {})
    )
    return {"status": "ok"}


@router.get("/events/{campaign_id}")
def fetch_events(campaign_id: int):
    return get_events(campaign_id)