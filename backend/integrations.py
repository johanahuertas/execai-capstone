from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict, Any, List
from datetime import datetime, timedelta

router = APIRouter(prefix="/integrations", tags=["integrations"])


# -----------------------
# MODELS
# -----------------------
class ListEventsRequest(BaseModel):
    provider: str  # "google" or "microsoft"
    days: int = 7


class CreateEventRequest(BaseModel):
    provider: str  # "google" or "microsoft"
    title: str
    start: str  # ISO datetime
    duration_min: int = 30


# -----------------------
# HELPERS (MOCK)
# -----------------------
def _mock_auth_url(provider: str) -> str:
    # Later: replace with real OAuth URL generator
    return f"https://example.com/oauth/{provider}?mock=true"


def _mock_events(days: int = 7) -> List[Dict[str, Any]]:
    now = datetime.now()
    return [
        {
            "id": "evt_mock_1",
            "title": "Mock Standup",
            "start": (now + timedelta(days=1, hours=9)).isoformat(),
            "end": (now + timedelta(days=1, hours=9, minutes=30)).isoformat(),
            "provider": "mock",
        },
        {
            "id": "evt_mock_2",
            "title": "Mock Client Call",
            "start": (now + timedelta(days=2, hours=14)).isoformat(),
            "end": (now + timedelta(days=2, hours=15)).isoformat(),
            "provider": "mock",
        },
    ]


# -----------------------
# ENDPOINTS
# -----------------------
@router.get("/status")
def integrations_status() -> Dict[str, Any]:
    """
    Capstone-friendly status endpoint.
    Later: you can wire this to OAuth token storage.
    """
    return {
        "google": {"connected": False, "mode": "mock"},
        "microsoft": {"connected": False, "mode": "mock"},
        "note": "OAuth is not enabled in MVP. Endpoints run in mock mode.",
    }


@router.get("/{provider}/auth-url")
def get_auth_url(provider: str) -> Dict[str, Any]:
    provider = provider.lower()
    if provider not in {"google", "microsoft"}:
        return {"error": "Unsupported provider. Use 'google' or 'microsoft'."}

    return {
        "provider": provider,
        "auth_url": _mock_auth_url(provider),
        "mode": "mock",
        "note": "Replace this with a real OAuth URL when ready.",
    }


@router.post("/{provider}/list-events")
def list_events(provider: str, payload: ListEventsRequest) -> Dict[str, Any]:
    provider = provider.lower()
    if provider not in {"google", "microsoft"}:
        return {"error": "Unsupported provider. Use 'google' or 'microsoft'."}

    # Keep payload.provider as debug/contract, but use path provider as truth
    events = _mock_events(days=payload.days)

    return {
        "provider": provider,
        "mode": "mock",
        "range_days": payload.days,
        "events": events,
    }


@router.post("/{provider}/create-event")
def create_event(provider: str, payload: CreateEventRequest) -> Dict[str, Any]:
    provider = provider.lower()
    if provider not in {"google", "microsoft"}:
        return {"error": "Unsupported provider. Use 'google' or 'microsoft'."}

    # Mock "created" event response
    return {
        "status": "created",
        "provider": provider,
        "mode": "mock",
        "event": {
            "id": "evt_created_mock",
            "title": payload.title,
            "start": payload.start,
            "duration_min": payload.duration_min,
        },
        "note": "This is a mock create. Later: call Google Calendar API / Microsoft Graph.",
    }
