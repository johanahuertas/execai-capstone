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
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

router = APIRouter(prefix="/integrations", tags=["integrations"])

TOKENS_DIR = Path(__file__).resolve().parent / ".tokens"
TOKENS_DIR.mkdir(exist_ok=True)
GOOGLE_TOKEN_PATH = TOKENS_DIR / "google_token.json"

_OAUTH_STATE = {"google": None}

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

DEFAULT_TZ = zoneinfo.ZoneInfo("America/New_York")


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


def _can_refresh(token: Dict[str, Any]) -> bool:
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
    refreshed["refresh_token"] = refresh_token
    _save_google_token(refreshed)

    return refreshed


def _get_google_access_token() -> str:
    token = _load_google_token()
    if not token:
        raise HTTPException(status_code=400, detail="Google not connected.")

    access = token.get("access_token")
    if not access:
        raise HTTPException(status_code=400, detail="Missing access_token. Reconnect Google.")

    return access


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
# SERVICES
# -----------------------

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


def create_event_service(
    provider: str,
    title: str,
    start: str,
    duration_min: int = 30,
    attendees: Optional[List[str]] = None,
    description: str = "",
    send_notifications: bool = True,
) -> Dict[str, Any]:
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


def get_freebusy_service(
    provider: str,
    time_min: str,
    time_max: str,
    calendar_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if provider != "google":
        raise HTTPException(status_code=400, detail="Only google supported")

    if calendar_ids is None:
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


def list_emails_service(
    provider: str,
    max_results: int = 10,
    inbox_only: bool = True,
    primary_only: bool = False,
) -> Dict[str, Any]:
    if provider != "google":
        raise HTTPException(status_code=400, detail="Only google supported")

    max_results = max(1, min(int(max_results), 20))

    query_parts = [
        "-in:drafts",
        "-in:sent",
        "-in:chats",
    ]

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
    if provider != "google":
        raise HTTPException(status_code=400, detail="Only google supported")

    message_id = (message_id or "").strip()
    if not message_id:
        raise HTTPException(status_code=400, detail="Missing message_id.")

    detail = _google_api_get(
        f"/gmail/v1/users/me/messages/{message_id}",
        {
            "format": "full",
        },
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
    if provider != "google":
        raise HTTPException(status_code=400, detail="Only google supported")

    to = (to or "").strip()
    subject = (subject or "").strip()
    body = body or ""

    if not to:
        raise HTTPException(status_code=400, detail="Missing recipient email.")
    if not subject:
        raise HTTPException(status_code=400, detail="Missing draft subject.")

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
        {
            "message": {
                "raw": encoded_message,
            }
        },
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
        "email": {
            "to": to,
            "subject": subject,
            "body": body,
        },
    }


def create_gmail_reply_draft_service(
    provider: str,
    to: str,
    subject: str,
    body: str,
    thread_id: str,
) -> Dict[str, Any]:
    if provider != "google":
        raise HTTPException(status_code=400, detail="Only google supported")

    to = (to or "").strip()
    subject = _reply_subject(subject)
    body = body or ""
    thread_id = (thread_id or "").strip()

    if not to:
        raise HTTPException(status_code=400, detail="Missing reply recipient email.")
    if not thread_id:
        raise HTTPException(status_code=400, detail="Missing thread_id for reply draft.")

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
        {
            "message": {
                "raw": encoded_message,
                "threadId": thread_id,
            }
        },
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
        "email": {
            "to": to,
            "subject": subject,
            "body": body,
        },
    }


# -----------------------
# ENDPOINTS
# -----------------------

@router.get("/status")
def status():
    return {"google_connected": bool(_load_google_token())}


@router.get("/google/auth-url")
def auth_url():
    return {"auth_url": _google_build_auth_url()}


@router.get("/google/callback", response_class=HTMLResponse)
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
    _OAUTH_STATE["google"] = None

    return """
    <html>
        <head>
            <title>ExecAI - Google Connected</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background: #f9fafb;
                    color: #111827;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                    margin: 0;
                }
                .card {
                    background: white;
                    padding: 32px;
                    border-radius: 16px;
                    box-shadow: 0 4px 16px rgba(0,0,0,0.08);
                    max-width: 500px;
                    text-align: center;
                }
                h1 {
                    margin-bottom: 12px;
                }
                p {
                    color: #4b5563;
                    line-height: 1.5;
                }
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
        attendees=payload.attendees,
        description=payload.description,
        send_notifications=payload.send_notifications,
    )


@router.post("/google/freebusy")
def freebusy(payload: FreeBusyRequest):
    return get_freebusy_service(
        "google",
        payload.time_min,
        payload.time_max,
        payload.calendar_ids,
    )


@router.get("/google/list-emails")
def list_emails(
    max_results: int = 10,
    inbox_only: bool = True,
    primary_only: bool = False,
):
    return list_emails_service(
        "google",
        max_results=max_results,
        inbox_only=inbox_only,
        primary_only=primary_only,
    )


@router.get("/google/read-email/{message_id}")
def read_email(message_id: str):
    return read_email_service("google", message_id)


@router.post("/google/create-draft")
def create_draft(payload: CreateDraftRequest):
    return create_gmail_draft_service(
        "google",
        payload.to,
        payload.subject,
        payload.body,
    )


@router.post("/google/create-reply-draft")
def create_reply_draft(payload: CreateReplyDraftRequest):
    return create_gmail_reply_draft_service(
        "google",
        payload.to,
        payload.subject,
        payload.body,
        payload.thread_id,
    )