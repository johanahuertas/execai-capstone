# backend/integrations.py

import os
import json
import secrets
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import zoneinfo

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/integrations", tags=["integrations"])

TOKENS_DIR = Path(__file__).resolve().parent / ".tokens"
TOKENS_DIR.mkdir(exist_ok=True)
GOOGLE_TOKEN_PATH = TOKENS_DIR / "google_token.json"

_OAUTH_STATE = {"google": None}

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

# Default timezone (change if needed)
DEFAULT_TZ = zoneinfo.ZoneInfo("America/New_York")

# ===============================
# MODELS
# ===============================

class ListEventsRequest(BaseModel):
    days: int = 7


class CreateEventRequest(BaseModel):
    title: str
    start: str  # ISO datetime
    duration_min: int = 30


# ===============================
# GOOGLE OAUTH HELPERS
# ===============================

def _google_config() -> Dict[str, str]:
    cid = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redirect = os.getenv("GOOGLE_REDIRECT_URI", "").strip()

    if not cid or not secret or not redirect:
        raise HTTPException(
            status_code=500,
            detail="Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI",
        )

    return {
        "client_id": cid,
        "client_secret": secret,
        "redirect_uri": redirect,
    }


def _google_build_auth_url() -> str:
    cfg = _google_config()
    state = secrets.token_urlsafe(24)
    _OAUTH_STATE["google"] = state

    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }

    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def _save_google_token(token: Dict[str, Any]) -> None:
    token = dict(token)
    token["saved_at"] = datetime.now(timezone.utc).isoformat()
    GOOGLE_TOKEN_PATH.write_text(json.dumps(token, indent=2), encoding="utf-8")


def _load_google_token() -> Optional[Dict[str, Any]]:
    if not GOOGLE_TOKEN_PATH.exists():
        return None
    return json.loads(GOOGLE_TOKEN_PATH.read_text(encoding="utf-8"))


def _is_token_expired(token: Dict[str, Any]) -> bool:
    expires_in = token.get("expires_in")
    saved_at = token.get("saved_at")

    if not expires_in or not saved_at:
        return True

    saved_dt = datetime.fromisoformat(saved_at)
    expiry = saved_dt + timedelta(seconds=int(expires_in))
    return datetime.now(timezone.utc) >= expiry


def _refresh_google_token(token: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _google_config()

    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": token["refresh_token"],
            "grant_type": "refresh_token",
        },
    )

    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=r.text)

    refreshed = r.json()
    refreshed["refresh_token"] = token["refresh_token"]
    _save_google_token(refreshed)
    return refreshed


def _get_google_access_token() -> str:
    token = _load_google_token()
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected.")

    if _is_token_expired(token):
        token = _refresh_google_token(token)

    return token["access_token"]


# ===============================
# GOOGLE API CALLS
# ===============================

def _google_api_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    token = _get_google_access_token()
    r = requests.get(
        f"https://www.googleapis.com{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
    )

    if r.status_code >= 400:
        raise HTTPException(status_code=400, detail=r.text)

    return r.json()


def _google_api_post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    token = _get_google_access_token()
    r = requests.post(
        f"https://www.googleapis.com{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
    )

    if r.status_code >= 400:
        raise HTTPException(status_code=400, detail=r.text)

    return r.json()


# ===============================
# SERVICES
# ===============================

def list_events_service(provider: str, days: int = 7):
    if provider != "google":
        raise HTTPException(status_code=400, detail="Only google supported")

    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days)).isoformat()

    data = _google_api_get(
        "/calendar/v3/calendars/primary/events",
        {
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime",
        },
    )

    events = []
    for e in data.get("items", []):
        events.append({
            "id": e.get("id"),
            "title": e.get("summary"),
            "start": e.get("start", {}).get("dateTime"),
            "end": e.get("end", {}).get("dateTime"),
            "htmlLink": e.get("htmlLink"),
        })

    return {"provider": "google", "events": events}


def create_event_service(provider: str, title: str, start: str, duration_min: int = 30):
    if provider != "google":
        raise HTTPException(status_code=400, detail="Only google supported")

    # Parse ISO datetime
    start_dt = datetime.fromisoformat(start)

    # If no timezone provided → attach default
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=DEFAULT_TZ)

    end_dt = start_dt + timedelta(minutes=duration_min)

    body = {
        "summary": title,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": str(start_dt.tzinfo),
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": str(end_dt.tzinfo),
        },
    }

    created = _google_api_post("/calendar/v3/calendars/primary/events", body)

    return {
        "status": "created",
        "event": {
            "id": created.get("id"),
            "title": created.get("summary"),
            "start": created.get("start", {}).get("dateTime"),
            "end": created.get("end", {}).get("dateTime"),
            "htmlLink": created.get("htmlLink"),
        },
    }


# ===============================
# ENDPOINTS
# ===============================

@router.get("/status")
def status():
    return {"google_connected": bool(_load_google_token())}


@router.get("/google/auth-url")
def auth_url():
    return {"auth_url": _google_build_auth_url()}


@router.get("/google/callback")
def callback(code: str, state: str):
    cfg = _google_config()

    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "redirect_uri": cfg["redirect_uri"],
            "grant_type": "authorization_code",
        },
    )

    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=r.text)

    _save_google_token(r.json())
    return {"status": "connected"}


@router.post("/google/list-events")
def list_events(payload: ListEventsRequest):
    return list_events_service("google", payload.days)


@router.post("/google/create-event")
def create_event(payload: CreateEventRequest):
    return create_event_service(
        "google",
        payload.title,
        payload.start,
        payload.duration_min,
    )
