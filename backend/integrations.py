# backend/integrations.py
import os
import json
import secrets
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/integrations", tags=["integrations"])

TOKENS_DIR = Path(__file__).resolve().parent / ".tokens"
TOKENS_DIR.mkdir(exist_ok=True)
GOOGLE_TOKEN_PATH = TOKENS_DIR / "google_token.json"

# In-memory state for OAuth (simple + capstone-friendly)
_OAUTH_STATE = {"google": None}


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


def _google_config() -> Dict[str, str]:
    cid = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redirect = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    if not cid or not secret or not redirect:
        raise HTTPException(
            status_code=500,
            detail="Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI env vars.",
        )
    return {"client_id": cid, "client_secret": secret, "redirect_uri": redirect}


def _google_build_auth_url() -> str:
    cfg = _google_config()

    # Minimal scopes for calendar work (we'll use these later)
    scopes = [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    ]

    state = secrets.token_urlsafe(24)
    _OAUTH_STATE["google"] = state

    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",          # to get refresh_token (usually on first consent)
        "prompt": "consent",               # forces refresh_token more reliably for demos
        "include_granted_scopes": "true",
        "state": state,
    }

    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def _save_google_token(token: Dict[str, Any]) -> None:
    GOOGLE_TOKEN_PATH.write_text(json.dumps(token, indent=2), encoding="utf-8")


def _load_google_token() -> Dict[str, Any] | None:
    if not GOOGLE_TOKEN_PATH.exists():
        return None
    try:
        return json.loads(GOOGLE_TOKEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


# -----------------------
# ENDPOINTS
# -----------------------
@router.get("/status")
def integrations_status() -> Dict[str, Any]:
    google_token = _load_google_token()
    return {
        "google": {"connected": bool(google_token), "mode": "oauth" if google_token else "mock"},
        "microsoft": {"connected": False, "mode": "mock"},
        "note": "Google OAuth is supported. Microsoft remains mock in this phase.",
    }


@router.get("/{provider}/auth-url")
def get_auth_url(provider: str) -> Dict[str, Any]:
    provider = provider.lower()
    if provider not in {"google", "microsoft"}:
        return {"error": "Unsupported provider. Use 'google' or 'microsoft'."}

    if provider == "google":
        auth_url = _google_build_auth_url()
        return {
            "provider": "google",
            "auth_url": auth_url,
            "mode": "oauth",
            "note": "Open this URL in a browser to connect Google.",
        }

    # microsoft stays mock for now
    return {
        "provider": "microsoft",
        "auth_url": _mock_auth_url("microsoft"),
        "mode": "mock",
        "note": "Microsoft OAuth will be added after Google is working.",
    }


@router.get("/google/callback")
def google_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' in callback.")
    if not state or state != _OAUTH_STATE.get("google"):
        raise HTTPException(status_code=400, detail="Invalid or missing OAuth 'state'.")

    cfg = _google_config()

    # Exchange code -> tokens
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri": cfg["redirect_uri"],
        "grant_type": "authorization_code",
    }

    r = requests.post(token_url, data=data, timeout=20)
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {r.text}")

    token = r.json()
    _save_google_token(token)

    return {
        "status": "connected",
        "provider": "google",
        "saved_to": str(GOOGLE_TOKEN_PATH),
        "has_refresh_token": bool(token.get("refresh_token")),
        "note": "Next step: use this token to call Google Calendar API endpoints.",
    }


@router.post("/{provider}/list-events")
def list_events(provider: str, payload: ListEventsRequest) -> Dict[str, Any]:
    provider = provider.lower()
    if provider not in {"google", "microsoft"}:
        return {"error": "Unsupported provider. Use 'google' or 'microsoft'."}

    # still mock in this phase
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

    # still mock in this phase
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
        "note": "Mock create. Next phase will call Google Calendar API / Microsoft Graph.",
    }
