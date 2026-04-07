import os
import re
import json
import secrets
import base64
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import zoneinfo

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

router = APIRouter(prefix="/integrations", tags=["integrations"])

TOKENS_DIR = Path(__file__).resolve().parent / ".tokens"
TOKENS_DIR.mkdir(exist_ok=True)

GOOGLE_TOKEN_PATH = TOKENS_DIR / "google_token.json"
OUTLOOK_TOKEN_PATH = TOKENS_DIR / "outlook_token.json"

_OAUTH_STATE = {
    "google": None,
    "outlook": None,
}

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

MICROSOFT_SCOPES = [
    "offline_access",
    "openid",
    "profile",
    "User.Read",
    "Calendars.Read",
    "Calendars.ReadWrite",
    "Mail.Read",
    "Mail.ReadWrite",
]

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
DEFAULT_TZ = zoneinfo.ZoneInfo("America/New_York")
OUTLOOK_TZ = "Eastern Standard Time"


# -----------------------
# MODELS
# -----------------------

class ListEventsRequest(BaseModel):
    days: int = 7


class CreateEventRequest(BaseModel):
    title: str
    start: str
    duration_min: int = 30
    attendees: List[str] = []
    description: str = ""
    send_notifications: bool = True


class FreeBusyRequest(BaseModel):
    time_min: str
    time_max: str
    calendar_ids: List[str] = ["primary"]


class ListEmailsRequest(BaseModel):
    max_results: int = 10


class CreateDraftRequest(BaseModel):
    to: str
    subject: str
    body: str


class ReadEmailRequest(BaseModel):
    message_id: str


class CreateReplyDraftRequest(BaseModel):
    to: str
    subject: str
    body: str
    thread_id: str


# -----------------------
# TOKEN HELPERS
# -----------------------

def _is_token_expired(token: Dict[str, Any], buffer_seconds: int = 300) -> bool:
    """
    Returns True if the token is expired or will expire within buffer_seconds.
    When in doubt (missing fields, parse errors), returns True to force a refresh.
    """
    if not token:
        return True

    saved_at_str = token.get("saved_at")
    expires_in = token.get("expires_in")

    if not saved_at_str or expires_in is None:
        # Can't determine expiry safely — treat as expired
        return True

    try:
        saved_dt = datetime.fromisoformat(saved_at_str)
        if saved_dt.tzinfo is None:
            saved_dt = saved_dt.replace(tzinfo=timezone.utc)
        expires_at = saved_dt + timedelta(seconds=int(expires_in))
        return datetime.now(timezone.utc) >= (expires_at - timedelta(seconds=buffer_seconds))
    except Exception:
        return True


# -----------------------
# GOOGLE OAUTH
# -----------------------

def _google_config(required: bool = True) -> Dict[str, str]:
    cid = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redirect = os.getenv("GOOGLE_REDIRECT_URI", "").strip()

    if required and (not cid or not secret or not redirect):
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


def _can_refresh_google(token: Dict[str, Any]) -> bool:
    refresh_token = (token or {}).get("refresh_token")
    if not refresh_token:
        return False
    cfg = _google_config(required=False)
    return bool(cfg["client_id"] and cfg["client_secret"] and cfg["redirect_uri"])


def _refresh_google_token(token: Dict[str, Any]) -> Dict[str, Any]:
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
            detail="Google token expired and refresh requires GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI.",
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
    # Google doesn't return a new refresh_token — preserve the original
    refreshed["refresh_token"] = refresh_token
    _save_google_token(refreshed)
    return refreshed


def _get_google_access_token() -> str:
    token = _load_google_token()
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected.")

    if _is_token_expired(token):
        if not _can_refresh_google(token):
            raise HTTPException(
                status_code=401,
                detail="Google token expired and cannot be refreshed. Reconnect Google.",
            )
        token = _refresh_google_token(token)

    access = token.get("access_token")
    if not access:
        raise HTTPException(status_code=400, detail="Missing Google access_token. Reconnect Google.")

    return access


# -----------------------
# OUTLOOK / MICROSOFT OAUTH
# -----------------------

def _microsoft_config(required: bool = True) -> Dict[str, str]:
    client_id = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
    client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "").strip()
    redirect_uri = os.getenv("MICROSOFT_REDIRECT_URI", "").strip()

    if required and (not client_id or not client_secret or not tenant_id or not redirect_uri):
        raise HTTPException(
            status_code=500,
            detail="Missing MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET / MICROSOFT_TENANT_ID / MICROSOFT_REDIRECT_URI",
        )

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "tenant_id": tenant_id,
        "redirect_uri": redirect_uri,
    }


def _microsoft_authorize_endpoint() -> str:
    cfg = _microsoft_config(required=True)
    return f"https://login.microsoftonline.com/{cfg['tenant_id']}/oauth2/v2.0/authorize"


def _microsoft_token_endpoint() -> str:
    cfg = _microsoft_config(required=True)
    return f"https://login.microsoftonline.com/{cfg['tenant_id']}/oauth2/v2.0/token"


def _microsoft_build_auth_url() -> str:
    cfg = _microsoft_config(required=True)
    state = secrets.token_urlsafe(24)
    _OAUTH_STATE["outlook"] = state

    params = {
        "client_id": cfg["client_id"],
        "response_type": "code",
        "redirect_uri": cfg["redirect_uri"],
        "response_mode": "query",
        "scope": " ".join(MICROSOFT_SCOPES),
        "state": state,
    }

    return _microsoft_authorize_endpoint() + "?" + urlencode(params)


def _save_outlook_token(token: Dict[str, Any]) -> None:
    token = dict(token)
    token["saved_at"] = datetime.now(timezone.utc).isoformat()
    OUTLOOK_TOKEN_PATH.write_text(json.dumps(token, indent=2), encoding="utf-8")


def _load_outlook_token() -> Optional[Dict[str, Any]]:
    if not OUTLOOK_TOKEN_PATH.exists():
        return None
    try:
        return json.loads(OUTLOOK_TOKEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _can_refresh_outlook(token: Dict[str, Any]) -> bool:
    refresh_token = (token or {}).get("refresh_token")
    if not refresh_token:
        return False
    cfg = _microsoft_config(required=False)
    return bool(
        cfg["client_id"] and cfg["client_secret"] and cfg["tenant_id"] and cfg["redirect_uri"]
    )


def _refresh_outlook_token(token: Dict[str, Any]) -> Dict[str, Any]:
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status_code=401,
            detail="Outlook token expired and no refresh_token available. Reconnect Outlook.",
        )

    cfg = _microsoft_config(required=False)
    if not (cfg["client_id"] and cfg["client_secret"] and cfg["tenant_id"] and cfg["redirect_uri"]):
        raise HTTPException(
            status_code=500,
            detail="Missing Microsoft OAuth env vars for token refresh.",
        )

    r = requests.post(
        _microsoft_token_endpoint(),
        data={
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "redirect_uri": cfg["redirect_uri"],
            "scope": " ".join(MICROSOFT_SCOPES),
        },
        timeout=20,
    )

    if r.status_code != 200:
        raise HTTPException(
            status_code=401,
            detail=f"Outlook token refresh failed: {r.text}",
        )

    refreshed = r.json()

    # Microsoft doesn't always return a new refresh_token — preserve the old one
    if "refresh_token" not in refreshed:
        refreshed["refresh_token"] = refresh_token

    # Stamps saved_at = now(), anchoring the new expires_in correctly
    _save_outlook_token(refreshed)
    return refreshed


def _get_outlook_access_token() -> str:
    """
    Returns a valid Outlook access token, refreshing automatically if expired.
    This is the single, authoritative implementation — no duplicates.
    """
    token = _load_outlook_token()
    if not token:
        raise HTTPException(status_code=400, detail="Outlook not connected.")

    if not token.get("access_token"):
        raise HTTPException(status_code=400, detail="Missing Outlook access_token. Reconnect Outlook.")

    if _is_token_expired(token):
        if not _can_refresh_outlook(token):
            raise HTTPException(
                status_code=401,
                detail="Outlook token expired and cannot be refreshed. Reconnect Outlook.",
            )
        token = _refresh_outlook_token(token)

    return token["access_token"]


# -----------------------
# GOOGLE API
# -----------------------

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
        if _can_refresh_google(saved):
            refreshed = _refresh_google_token(saved)
            r = requests.get(
                url,
                headers={"Authorization": f"Bearer {refreshed['access_token']}"},
                params=params,
                timeout=20,
            )

    if r.status_code >= 400:
        if r.status_code == 401:
            raise HTTPException(status_code=400, detail="Google access token expired. Reconnect Google.")
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
        if _can_refresh_google(saved):
            refreshed = _refresh_google_token(saved)
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {refreshed['access_token']}", "Content-Type": "application/json"},
                json=body,
                timeout=20,
            )

    if r.status_code >= 400:
        if r.status_code == 401:
            raise HTTPException(status_code=400, detail="Google access token expired. Reconnect Google.")
        raise HTTPException(status_code=400, detail=r.text)

    return r.json()


# -----------------------
# MICROSOFT GRAPH API
# -----------------------

def _graph_api_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{GRAPH_BASE_URL}{path}"

    def _do_get(access_token: str) -> requests.Response:
        return requests.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Prefer": f'outlook.timezone="{OUTLOOK_TZ}"',
            },
            params=params or {},
            timeout=20,
        )

    r = _do_get(_get_outlook_access_token())

    # If Microsoft still rejects the token (clock skew, early revocation), force one refresh
    if r.status_code == 401:
        token = _load_outlook_token()
        if token and _can_refresh_outlook(token):
            refreshed = _refresh_outlook_token(token)
            r = _do_get(refreshed["access_token"])

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    return r.json()


def _graph_api_post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{GRAPH_BASE_URL}{path}"

    def _do_post(access_token: str) -> requests.Response:
        return requests.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Prefer": f'outlook.timezone="{OUTLOOK_TZ}"',
            },
            json=body,
            timeout=20,
        )

    r = _do_post(_get_outlook_access_token())

    if r.status_code == 401:
        token = _load_outlook_token()
        if token and _can_refresh_outlook(token):
            refreshed = _refresh_outlook_token(token)
            r = _do_post(refreshed["access_token"])

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    return r.json()


def _graph_api_patch(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{GRAPH_BASE_URL}{path}"

    def _do_patch(access_token: str) -> requests.Response:
        return requests.patch(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=20,
        )

    r = _do_patch(_get_outlook_access_token())

    if r.status_code == 401:
        token = _load_outlook_token()
        if token and _can_refresh_outlook(token):
            refreshed = _refresh_outlook_token(token)
            r = _do_patch(refreshed["access_token"])

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    return r.json()


def _get_outlook_schedule_targets(calendar_ids: Optional[List[str]] = None) -> List[str]:
    clean_ids = [
        cid.strip()
        for cid in (calendar_ids or [])
        if cid and cid.strip() and cid.strip().lower() != "primary"
    ]
    if clean_ids:
        return clean_ids

    me = _graph_api_get("/me", {"$select": "mail,userPrincipalName"})
    email = (me.get("mail") or "").strip() or (me.get("userPrincipalName") or "").strip()

    if not email:
        raise HTTPException(
            status_code=400,
            detail="Could not determine Outlook mailbox address for free/busy lookup.",
        )

    return [email]


# -----------------------
# GMAIL HELPERS
# -----------------------

def _decode_gmail_base64(data: str) -> str:
    if not data:
        return ""
    try:
        padding = "=" * (-len(data) % 4)
        decoded = base64.urlsafe_b64decode(data + padding)
        return decoded.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_gmail_body(payload: Dict[str, Any]) -> str:
    if not payload:
        return ""

    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {}) or {}
    data = body.get("data")

    if data and mime_type in {"text/plain", "text/html"}:
        return _decode_gmail_base64(data)

    parts = payload.get("parts", []) or []
    if parts:
        for part in parts:
            if part.get("mimeType") == "text/plain":
                part_data = (part.get("body", {}) or {}).get("data")
                if part_data:
                    return _decode_gmail_base64(part_data)
        for part in parts:
            if part.get("mimeType") == "text/html":
                part_data = (part.get("body", {}) or {}).get("data")
                if part_data:
                    return _decode_gmail_base64(part_data)
        for part in parts:
            nested = _extract_gmail_body(part)
            if nested:
                return nested

    if data:
        return _decode_gmail_base64(data)

    return ""


def _headers_to_map(headers: List[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for h in headers or []:
        name = h.get("name")
        value = h.get("value")
        if name and value:
            out[name.lower()] = value
    return out


def _extract_email_address(raw_value: Optional[str]) -> str:
    if not raw_value:
        return ""
    m = re.search(r"<([^>]+)>", raw_value)
    if m:
        return m.group(1).strip().lower()
    m2 = re.search(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b", raw_value)
    if m2:
        return m2.group(0).strip().lower()
    return raw_value.strip().lower()


def _reply_subject(subject: Optional[str]) -> str:
    s = (subject or "").strip()
    if not s:
        return "Re: Quick Follow-Up"
    if s.lower().startswith("re:"):
        return s
    return f"Re: {s}"


# -----------------------
# SHARED SERVICES
# -----------------------

def list_events_service(provider: str, days: int = 7) -> Dict[str, Any]:
    provider = (provider or "").strip().lower()
    days = max(1, min(int(days), 31))

    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days)).isoformat()

    if provider == "google":
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
            events.append(
                {
                    "id": e.get("id"),
                    "title": e.get("summary"),
                    "start": start_obj.get("dateTime") or start_obj.get("date"),
                    "end": end_obj.get("dateTime") or end_obj.get("date"),
                    "htmlLink": e.get("htmlLink"),
                }
            )

        return {"provider": "google", "events": events}

    if provider == "outlook":
        data = _graph_api_get(
            "/me/calendar/calendarView",
            {
                "startDateTime": time_min,
                "endDateTime": time_max,
                "$orderby": "start/dateTime",
                "$top": 50,
            },
        )

        events = []
        for e in data.get("value", []):
            events.append(
                {
                    "id": e.get("id"),
                    "title": e.get("subject"),
                    "start": ((e.get("start") or {}).get("dateTime")),
                    "end": ((e.get("end") or {}).get("dateTime")),
                    "htmlLink": e.get("webLink"),
                }
            )

        return {"provider": "outlook", "events": events}

    raise HTTPException(status_code=400, detail="Unsupported provider. Use google or outlook.")


def create_event_service(
    provider: str,
    title: str,
    start: str,
    duration_min: int = 30,
    attendees: Optional[List[str]] = None,
    description: str = "",
    send_notifications: bool = True,
) -> Dict[str, Any]:
    provider = (provider or "").strip().lower()
    duration_min = max(5, min(int(duration_min), 240))

    start_dt = datetime.fromisoformat(start)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=DEFAULT_TZ)

    end_dt = start_dt + timedelta(minutes=duration_min)
    attendees = attendees or []

    if provider == "google":
        body = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": str(start_dt.tzinfo)},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": str(end_dt.tzinfo)},
        }

        if description:
            body["description"] = description

        if attendees:
            body["attendees"] = [{"email": email.strip()} for email in attendees if email and email.strip()]

        query_params = ""
        if attendees and send_notifications:
            query_params = "?sendUpdates=all"
        elif attendees:
            query_params = "?sendUpdates=none"

        created = _google_api_post(f"/calendar/v3/calendars/primary/events{query_params}", body)

        return {
            "status": "created",
            "provider": "google",
            "event": {
                "id": created.get("id"),
                "title": created.get("summary"),
                "start": (created.get("start", {}) or {}).get("dateTime") or (created.get("start", {}) or {}).get("date"),
                "end": (created.get("end", {}) or {}).get("dateTime") or (created.get("end", {}) or {}).get("date"),
                "htmlLink": created.get("htmlLink"),
                "attendees": [
                    {"email": a.get("email"), "status": a.get("responseStatus", "needsAction")}
                    for a in (created.get("attendees") or [])
                ],
            },
        }

    if provider == "outlook":
        body = {
            "subject": title,
            "start": {
                "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": OUTLOOK_TZ,
            },
            "end": {
                "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": OUTLOOK_TZ,
            },
        }

        if description:
            body["body"] = {"contentType": "text", "content": description}

        if attendees:
            body["attendees"] = [
                {
                    "emailAddress": {"address": email.strip()},
                    "type": "required",
                }
                for email in attendees
                if email and email.strip()
            ]

        created = _graph_api_post("/me/events", body)

        return {
            "status": "created",
            "provider": "outlook",
            "event": {
                "id": created.get("id"),
                "title": created.get("subject"),
                "start": ((created.get("start") or {}).get("dateTime")),
                "end": ((created.get("end") or {}).get("dateTime")),
                "htmlLink": created.get("webLink"),
                "attendees": [
                    {
                        "email": ((a.get("emailAddress") or {}).get("address")),
                        "status": (((a.get("status") or {}).get("response")) or "none"),
                    }
                    for a in (created.get("attendees") or [])
                ],
            },
        }

    raise HTTPException(status_code=400, detail="Unsupported provider. Use google or outlook.")


def get_freebusy_service(
    provider: str,
    time_min: str,
    time_max: str,
    calendar_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    provider = (provider or "").strip().lower()

    if provider == "google":
        if calendar_ids is None or not calendar_ids:
            calendar_ids = ["primary"]

        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": str(DEFAULT_TZ),
            "items": [{"id": cid} for cid in calendar_ids],
        }

        data = _google_api_post("/calendar/v3/freeBusy", body)

        busy_blocks: List[Dict[str, str]] = []
        calendars = data.get("calendars", {})

        for cal_id in calendar_ids:
            cal_data = calendars.get(cal_id, {})
            for block in cal_data.get("busy", []):
                busy_blocks.append(
                    {
                        "start": block.get("start", ""),
                        "end": block.get("end", ""),
                    }
                )

        busy_blocks.sort(key=lambda b: b.get("start", ""))

        return {
            "provider": "google",
            "time_min": time_min,
            "time_max": time_max,
            "busy_blocks": busy_blocks,
        }

    if provider == "outlook":
        try:
            start_dt = datetime.fromisoformat(time_min)
            end_dt = datetime.fromisoformat(time_max)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Outlook freebusy datetime format.")

        schedule_targets = _get_outlook_schedule_targets(calendar_ids)

        body = {
            "schedules": schedule_targets,
            "startTime": {
                "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": OUTLOOK_TZ,
            },
            "endTime": {
                "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": OUTLOOK_TZ,
            },
            "availabilityViewInterval": 30,
        }

        data = _graph_api_post("/me/calendar/getSchedule", body)

        busy_blocks: List[Dict[str, str]] = []

        for sched in data.get("value", []):
            for item in sched.get("scheduleItems", []):
                status = (item.get("status") or "").lower()
                if status in {"busy", "oof", "tentative", "workingelsewhere"}:
                    busy_blocks.append(
                        {
                            "start": ((item.get("start") or {}).get("dateTime", "")),
                            "end": ((item.get("end") or {}).get("dateTime", "")),
                        }
                    )

        busy_blocks.sort(key=lambda b: b.get("start", ""))

        return {
            "provider": "outlook",
            "time_min": time_min,
            "time_max": time_max,
            "busy_blocks": busy_blocks,
        }

    raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")


# -----------------------
# GMAIL SERVICES
# -----------------------

def list_emails_service(
    provider: str,
    max_results: int = 10,
    inbox_only: bool = True,
    primary_only: bool = False,
) -> Dict[str, Any]:
    provider = (provider or "google").strip().lower()
    max_results = max(1, min(int(max_results), 20))

    if provider == "outlook":
        params: Dict[str, Any] = {
            "$top": max_results,
            "$select": "id,conversationId,subject,from,toRecipients,receivedDateTime,bodyPreview",
            "$orderby": "receivedDateTime desc",
        }
        if inbox_only:
            data = _graph_api_get("/me/mailFolders/inbox/messages", params)
        else:
            data = _graph_api_get("/me/messages", params)

        emails = []
        for msg in data.get("value", []):
            from_obj = (msg.get("from") or {}).get("emailAddress") or {}
            to_list = msg.get("toRecipients") or []
            to_addr = ", ".join(
                (r.get("emailAddress") or {}).get("address", "")
                for r in to_list
            )
            emails.append({
                "id": msg.get("id"),
                "threadId": msg.get("conversationId"),
                "from": f"{from_obj.get('name', '')} <{from_obj.get('address', '')}>".strip(),
                "to": to_addr,
                "subject": msg.get("subject"),
                "date": msg.get("receivedDateTime"),
                "snippet": msg.get("bodyPreview"),
                "labelIds": [],
            })

        return {"provider": "outlook", "emails": emails, "query_used": "inbox"}

    # Google
    query_parts = ["-in:drafts", "-in:sent", "-in:chats"]

    if primary_only:
        query_parts.append("category:primary")
    elif inbox_only:
        query_parts.append("in:inbox")

    gmail_query = " ".join(query_parts).strip()

    data = _google_api_get(
        "/gmail/v1/users/me/messages",
        {
            "maxResults": max_results,
            "q": gmail_query,
        },
    )

    messages = data.get("messages", []) or []
    emails = []

    for msg in messages:
        msg_id = msg.get("id")
        if not msg_id:
            continue

        detail = _google_api_get(
            f"/gmail/v1/users/me/messages/{msg_id}",
            {
                "format": "metadata",
                "metadataHeaders": ["From", "To", "Subject", "Date"],
            },
        )

        headers = (detail.get("payload", {}) or {}).get("headers", []) or []
        header_map = _headers_to_map(headers)
        label_ids = detail.get("labelIds", []) or []

        if "DRAFT" in label_ids or "SENT" in label_ids:
            continue

        emails.append(
            {
                "id": msg_id,
                "threadId": detail.get("threadId"),
                "from": header_map.get("from"),
                "to": header_map.get("to"),
                "subject": header_map.get("subject"),
                "date": header_map.get("date"),
                "snippet": detail.get("snippet"),
                "labelIds": label_ids,
            }
        )

    return {
        "provider": "google",
        "emails": emails,
        "query_used": gmail_query,
    }


def read_email_service(provider: str, message_id: str) -> Dict[str, Any]:
    provider = (provider or "google").strip().lower()
    message_id = (message_id or "").strip()
    if not message_id:
        raise HTTPException(status_code=400, detail="Missing message_id.")

    if provider == "outlook":
        detail = _graph_api_get(
            f"/me/messages/{message_id}",
            {"$select": "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview,body"},
        )
        from_obj = (detail.get("from") or {}).get("emailAddress") or {}
        to_list = detail.get("toRecipients") or []
        cc_list = detail.get("ccRecipients") or []
        to_addr = ", ".join((r.get("emailAddress") or {}).get("address", "") for r in to_list)
        cc_addr = ", ".join((r.get("emailAddress") or {}).get("address", "") for r in cc_list)
        body_content = (detail.get("body") or {}).get("content") or detail.get("bodyPreview") or ""

        return {
            "provider": "outlook",
            "email": {
                "id": detail.get("id"),
                "threadId": detail.get("conversationId"),
                "labelIds": [],
                "snippet": detail.get("bodyPreview"),
                "from": f"{from_obj.get('name', '')} <{from_obj.get('address', '')}>".strip(),
                "to": to_addr,
                "cc": cc_addr,
                "subject": detail.get("subject"),
                "date": detail.get("receivedDateTime"),
                "body": body_content,
            },
        }

    # Google
    detail = _google_api_get(
        f"/gmail/v1/users/me/messages/{message_id}",
        {"format": "full"},
    )

    payload = detail.get("payload", {}) or {}
    headers = payload.get("headers", []) or []
    header_map = _headers_to_map(headers)
    body_text = _extract_gmail_body(payload)

    return {
        "provider": "google",
        "email": {
            "id": detail.get("id"),
            "threadId": detail.get("threadId"),
            "labelIds": detail.get("labelIds", []),
            "snippet": detail.get("snippet"),
            "from": header_map.get("from"),
            "to": header_map.get("to"),
            "cc": header_map.get("cc"),
            "subject": header_map.get("subject"),
            "date": header_map.get("date"),
            "body": body_text,
        },
    }


def create_gmail_draft_service(provider: str, to: str, subject: str, body: str) -> Dict[str, Any]:
    provider = (provider or "google").strip().lower()
    to = (to or "").strip()
    subject = (subject or "").strip()
    body = body or ""

    if not to:
        raise HTTPException(status_code=400, detail="Missing recipient email.")
    if not subject:
        raise HTTPException(status_code=400, detail="Missing draft subject.")

    if provider == "outlook":
        created = _graph_api_post(
            "/me/messages",
            {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
        )
        # Save as draft by keeping it unsent (Graph creates as draft by default via POST /me/messages)
        return {
            "status": "draft_created",
            "draft": {
                "id": created.get("id"),
                "messageId": created.get("id"),
                "threadId": created.get("conversationId"),
                "labelIds": [],
            },
            "email": {"to": to, "subject": subject, "body": body},
        }

    # Google
    raw_message = (
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        "Content-Type: text/plain; charset=UTF-8\r\n"
        "\r\n"
        f"{body}"
    )

    encoded_message = base64.urlsafe_b64encode(raw_message.encode("utf-8")).decode("utf-8")

    created = _google_api_post(
        "/gmail/v1/users/me/drafts",
        {"message": {"raw": encoded_message}},
    )

    draft = created.get("message", {}) or {}

    return {
        "status": "draft_created",
        "draft": {
            "id": created.get("id"),
            "messageId": draft.get("id"),
            "threadId": draft.get("threadId"),
            "labelIds": draft.get("labelIds", []),
        },
        "email": {"to": to, "subject": subject, "body": body},
    }


def create_gmail_reply_draft_service(
    provider: str,
    to: str,
    subject: str,
    body: str,
    thread_id: str,
) -> Dict[str, Any]:
    provider = (provider or "google").strip().lower()
    to = (to or "").strip()
    subject = _reply_subject(subject)
    body = body or ""
    thread_id = (thread_id or "").strip()

    if not to:
        raise HTTPException(status_code=400, detail="Missing reply recipient email.")
    if not thread_id:
        raise HTTPException(status_code=400, detail="Missing thread_id for reply draft.")

    if provider == "outlook":
        # In Graph API, create a reply draft from the original message
        created = _graph_api_post(
            f"/me/messages/{thread_id}/createReply",
            {},
        )
        # Update the reply draft body
        reply_id = created.get("id")
        if reply_id:
            _graph_api_patch(f"/me/messages/{reply_id}", {
                "body": {"contentType": "Text", "content": body},
            })
        return {
            "status": "reply_draft_created",
            "draft": {
                "id": reply_id,
                "messageId": reply_id,
                "threadId": created.get("conversationId") or thread_id,
                "labelIds": [],
            },
            "email": {"to": to, "subject": subject, "body": body},
        }

    # Google
    raw_message = (
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        "Content-Type: text/plain; charset=UTF-8\r\n"
        "\r\n"
        f"{body}"
    )

    encoded_message = base64.urlsafe_b64encode(raw_message.encode("utf-8")).decode("utf-8")

    created = _google_api_post(
        "/gmail/v1/users/me/drafts",
        {"message": {"raw": encoded_message, "threadId": thread_id}},
    )

    draft = created.get("message", {}) or {}

    return {
        "status": "reply_draft_created",
        "draft": {
            "id": created.get("id"),
            "messageId": draft.get("id"),
            "threadId": draft.get("threadId"),
            "labelIds": draft.get("labelIds", []),
        },
        "email": {"to": to, "subject": subject, "body": body},
    }


# -----------------------
# ENDPOINTS
# -----------------------

@router.get("/status")
def status():
    return {
        "google_connected": bool(_load_google_token()),
        "outlook_connected": bool(_load_outlook_token()),
    }


@router.get("/google/auth-url")
def google_auth_url():
    return RedirectResponse(url=_google_build_auth_url())


@router.get("/google/callback", response_class=HTMLResponse)
def google_callback(code: str, state: str):
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
    _OAUTH_STATE["google"] = None

    return """
    <html>
        <head>
            <title>ExecAI - Google Connected</title>
            <style>
                body { font-family: Arial, sans-serif; background: #f9fafb; color: #111827;
                       display: flex; align-items: center; justify-content: center;
                       min-height: 100vh; margin: 0; }
                .card { background: white; padding: 32px; border-radius: 16px;
                        box-shadow: 0 4px 16px rgba(0,0,0,0.08); max-width: 500px; text-align: center; }
                h1 { margin-bottom: 12px; }
                p { color: #4b5563; line-height: 1.5; }
            </style>
        </head>
        <body>
            <div class="card">
                <h1>Google connected successfully</h1>
                <p>Your Google account is now linked to ExecAI.</p>
                <p>You can close this window and return to the app.</p>
            </div>
        </body>
    </html>
    """


@router.get("/outlook/auth-url")
def outlook_auth_url():
    return RedirectResponse(url=_microsoft_build_auth_url())


@router.get("/outlook/callback", response_class=HTMLResponse)
def outlook_callback(code: str, state: str):
    if not state or state != _OAUTH_STATE.get("outlook"):
        raise HTTPException(status_code=400, detail="Invalid or missing Outlook OAuth state.")

    cfg = _microsoft_config(required=True)

    r = requests.post(
        _microsoft_token_endpoint(),
        data={
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "code": code,
            "redirect_uri": cfg["redirect_uri"],
            "grant_type": "authorization_code",
            "scope": " ".join(MICROSOFT_SCOPES),
        },
        timeout=20,
    )

    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=r.text)

    _save_outlook_token(r.json())
    _OAUTH_STATE["outlook"] = None

    return """
    <html>
        <head>
            <title>ExecAI - Outlook Connected</title>
            <style>
                body { font-family: Arial, sans-serif; background: #f9fafb; color: #111827;
                       display: flex; align-items: center; justify-content: center;
                       min-height: 100vh; margin: 0; }
                .card { background: white; padding: 32px; border-radius: 16px;
                        box-shadow: 0 4px 16px rgba(0,0,0,0.08); max-width: 500px; text-align: center; }
                h1 { margin-bottom: 12px; }
                p { color: #4b5563; line-height: 1.5; }
            </style>
        </head>
        <body>
            <div class="card">
                <h1>Outlook connected successfully</h1>
                <p>Your Outlook account is now linked to ExecAI.</p>
                <p>You can close this window and return to the app.</p>
            </div>
        </body>
    </html>
    """


@router.post("/google/list-events")
def google_list_events(payload: ListEventsRequest):
    return list_events_service("google", payload.days)


@router.post("/google/create-event")
def google_create_event(payload: CreateEventRequest):
    return create_event_service(
        "google",
        payload.title,
        payload.start,
        payload.duration_min,
        attendees=payload.attendees,
        description=payload.description,
        send_notifications=payload.send_notifications,
    )


@router.post("/google/freebusy")
def google_freebusy(payload: FreeBusyRequest):
    return get_freebusy_service("google", payload.time_min, payload.time_max, payload.calendar_ids)


@router.post("/outlook/list-events")
def outlook_list_events(payload: ListEventsRequest):
    return list_events_service("outlook", payload.days)


@router.post("/outlook/create-event")
def outlook_create_event(payload: CreateEventRequest):
    return create_event_service(
        "outlook",
        payload.title,
        payload.start,
        payload.duration_min,
        attendees=payload.attendees,
        description=payload.description,
        send_notifications=payload.send_notifications,
    )


@router.post("/outlook/freebusy")
def outlook_freebusy(payload: FreeBusyRequest):
    return get_freebusy_service("outlook", payload.time_min, payload.time_max, payload.calendar_ids)


@router.get("/google/list-emails")
def list_emails(
    max_results: int = 10,
    inbox_only: bool = True,
    primary_only: bool = False,
):
    return list_emails_service("google", max_results=max_results, inbox_only=inbox_only, primary_only=primary_only)


@router.get("/google/read-email/{message_id}")
def read_email(message_id: str):
    return read_email_service("google", message_id)


@router.post("/google/create-draft")
def create_draft(payload: CreateDraftRequest):
    return create_gmail_draft_service("google", payload.to, payload.subject, payload.body)


@router.post("/google/create-reply-draft")
def create_reply_draft(payload: CreateReplyDraftRequest):
    return create_gmail_reply_draft_service(
        "google", payload.to, payload.subject, payload.body, payload.thread_id
    )