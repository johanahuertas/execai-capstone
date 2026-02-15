# backend/integrations.py

import os
import json
import secrets
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import zoneinfo

# Option 1: load .env (but don't crash if python-dotenv isn't installed)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

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


def _google_config(required: bool = True) -> Dict[str, str]:
    cid = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redirect = os.getenv("GOOGLE_REDIRECT_URI", "").strip()

    if required and (not cid or not secret or not redirect):
        raise HTTPException(
            status_code=500,
            detail="Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI",
        )

    return {"client_id": cid, "client_secret": secret, "redirect_uri": redirect}


def _google_build_auth_url() -> str:
    cfg = _google_config(required=True)
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
    try:
        return json.loads(GOOGLE_TOKEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _can_refresh(token: Dict[str, Any]) -> bool:
    refresh_token = (token or {}).get("refresh_token")
    if not refresh_token:
        return False

    cfg = _google_config(required=False)
    return bool(cfg["client_id"] and cfg["client_secret"] and cfg["redirect_uri"])


def _refresh_google_token(token: Dict[str, Any]) -> Dict[str, Any]:
    """
    Refresh ONLY when we *actually need it* (after a 401),
    and only if env vars + refresh_token exist.
    """
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status_code=400,
            detail="Google token expired and no refresh_token is available. Reconnect Google.",
        )

    cfg = _google_config(required=False)
    if not (cfg["client_id"] and cfg["client_secret"] and cfg["redirect_uri"]):
        raise HTTPException(
            status_code=400,
            detail="Google token expired and refresh requires GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI. Set them in .env or reconnect Google.",
        )

    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )

    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=r.text)

    refreshed = r.json()
    refreshed["refresh_token"] = refresh_token  # keep it
    _save_google_token(refreshed)
    return refreshed


def _get_google_access_token() -> str:
    """
    IMPORTANT: Do NOT require env vars for normal usage.
    Just return the saved access token. Refresh happens only on 401.
    """
    token = _load_google_token()
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected.")

    access = token.get("access_token")
    if not access:
        raise HTTPException(status_code=400, detail="Missing access_token. Reconnect Google.")

    return access


# ===============================
# GOOGLE API CALLS (retry on 401)
# ===============================


def _google_api_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    url = f"https://www.googleapis.com{path}"
    token = _get_google_access_token()

    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=20,
    )

    if r.status_code == 401:
        saved = _load_google_token() or {}
        if _can_refresh(saved):
            refreshed = _refresh_google_token(saved)
            token2 = refreshed.get("access_token")
            r = requests.get(
                url,
                headers={"Authorization": f"Bearer {token2}"},
                params=params,
                timeout=20,
            )

    if r.status_code >= 400:
        if r.status_code == 401:
            raise HTTPException(
                status_code=400,
                detail="Google access token expired. Reconnect Google (or set GOOGLE_CLIENT_* env vars to refresh).",
            )
        raise HTTPException(status_code=400, detail=r.text)

    return r.json()


def _google_api_post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    url = f"https://www.googleapis.com{path}"
    token = _get_google_access_token()

    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=20,
    )

    if r.status_code == 401:
        saved = _load_google_token() or {}
        if _can_refresh(saved):
            refreshed = _refresh_google_token(saved)
            token2 = refreshed.get("access_token")
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {token2}", "Content-Type": "application/json"},
                json=body,
                timeout=20,
            )

    if r.status_code >= 400:
        if r.status_code == 401:
            raise HTTPException(
                status_code=400,
                detail="Google access token expired. Reconnect Google (or set GOOGLE_CLIENT_* env vars to refresh).",
            )
        raise HTTPException(status_code=400, detail=r.text)

    return r.json()


# ===============================
# SERVICES
# ===============================


def list_events_service(provider: str, days: int = 7) -> Dict[str, Any]:
    if provider != "google":
        raise HTTPException(status_code=400, detail="Only google supported")

    days = max(1, min(int(days), 31))

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
        start_obj = e.get("start", {}) or {}
        end_obj = e.get("end", {}) or {}

        start_val = start_obj.get("dateTime") or start_obj.get("date")
        end_val = end_obj.get("dateTime") or end_obj.get("date")

        events.append(
            {
                "id": e.get("id"),
                "title": e.get("summary"),
                "start": start_val,
                "end": end_val,
                "htmlLink": e.get("htmlLink"),
            }
        )

    return {"provider": "google", "events": events}


def create_event_service(provider: str, title: str, start: str, duration_min: int = 30) -> Dict[str, Any]:
    if provider != "google":
        raise HTTPException(status_code=400, detail="Only google supported")

    duration_min = max(5, min(int(duration_min), 240))

    start_dt = datetime.fromisoformat(start)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=DEFAULT_TZ)

    end_dt = start_dt + timedelta(minutes=duration_min)

    body = {
        "summary": title,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": str(start_dt.tzinfo)},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": str(end_dt.tzinfo)},
    }

    created = _google_api_post("/calendar/v3/calendars/primary/events", body)

    return {
        "status": "created",
        "event": {
            "id": created.get("id"),
            "title": created.get("summary"),
            "start": (created.get("start", {}) or {}).get("dateTime") or (created.get("start", {}) or {}).get("date"),
            "end": (created.get("end", {}) or {}).get("dateTime") or (created.get("end", {}) or {}).get("date"),
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
    if not state or state != _OAUTH_STATE.get("google"):
        raise HTTPException(status_code=400, detail="Invalid or missing OAuth state.")

    cfg = _google_config(required=True)

    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "redirect_uri": cfg["redirect_uri"],
            "grant_type": "authorization_code",
        },
        timeout=20,
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
    return create_event_service("google", payload.title, payload.start, payload.duration_min)
