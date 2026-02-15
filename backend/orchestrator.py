# backend/orchestrator.py
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from zoneinfo import ZoneInfo

DEFAULT_PROVIDER = "google"
DEFAULT_TZ = ZoneInfo("America/New_York")  # cámbialo si quieres


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _infer_event_title(raw_text: str) -> str:
    """
    Heurística simple:
    - "create event called X" -> X
    - Limpia timeframe al final (tomorrow/today/next week/this week)
    - Si no encuentra, título genérico
    """
    t = (raw_text or "").strip()
    if not t:
        return "New Event"

    title = None

    # called "X"
    m = re.search(r'called\s+"([^"]+)"', t, re.IGNORECASE)
    if m:
        title = m.group(1).strip()

    # called X...
    if not title:
        m2 = re.search(r"called\s+(.+)", t, re.IGNORECASE)
        if m2:
            title = m2.group(1).strip().strip('"').strip("'")

    if not title:
        return "ExecAI Event"

    # remove trailing timeframe words/phrases
    title = re.sub(r"\b(tomorrow|today)\b\s*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\b(next week|this week)\b\s*$", "", title, flags=re.IGNORECASE).strip()

    # clean trailing punctuation/spaces
    title = title.strip(" -,:;")

    return title[:80] if title else "ExecAI Event"


def _parse_time_from_text(text: str) -> Optional[Tuple[int, int]]:
    """
    Detecta 'at 2pm', '2:30 pm', '14:00'
    Devuelve (hour, minute) o None.
    """
    t = (text or "").lower()

    # 2pm / 2 pm / 2:30pm / 2:30 pm
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or "0")
        ampm = m.group(3)

        if hour < 1 or hour > 12:
            return None
        if minute < 0 or minute > 59:
            return None

        if hour == 12:
            hour = 0
        if ampm == "pm":
            hour += 12

        return hour, minute

    # 14:00 (24h)
    m2 = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if m2:
        hour = int(m2.group(1))
        minute = int(m2.group(2))
        return hour, minute

    return None


def _default_start_from_timeframe(timeframe: Optional[str], raw_text: str, tz: ZoneInfo) -> datetime:
    """
    Si no hay fecha/hora explícita:
    - tomorrow -> mañana 10:00 (o la hora detectada)
    - today -> hoy + 1 hora redondeada, o la hora detectada
    - fallback -> hoy a la hora detectada, o +1 hora redondeada
    """
    now = datetime.now(tz)
    tf = (timeframe or "").lower().strip()

    parsed_time = _parse_time_from_text(raw_text)

    if tf == "tomorrow":
        d = (now + timedelta(days=1)).date()
        hour, minute = parsed_time if parsed_time else (10, 0)
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)

    if tf == "today":
        if parsed_time:
            d = now.date()
            hour, minute = parsed_time
            dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)
            # si ya pasó hoy, lo mandamos a +1 hora redondeada (demo-friendly)
            if dt <= now:
                start = now + timedelta(hours=1)
                return start.replace(minute=0, second=0, microsecond=0)
            return dt

        start = now + timedelta(hours=1)
        return start.replace(minute=0, second=0, microsecond=0)

    # fallback general
    if parsed_time:
        d = now.date()
        hour, minute = parsed_time
        dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)
        if dt <= now:
            d2 = (now + timedelta(days=1)).date()
            dt = datetime(d2.year, d2.month, d2.day, hour, minute, tzinfo=tz)
        return dt

    start = now + timedelta(hours=1)
    return start.replace(minute=0, second=0, microsecond=0)


def handle_intent(intent_data: dict) -> dict:
    intent = (intent_data or {}).get("intent") or "unknown"
    entities: Dict[str, Any] = (intent_data or {}).get("entities") or {}
    original_text = (intent_data or {}).get("original_text") or ""

    # ---------- CALENDAR: LIST EVENTS ----------
    if intent == "list_events":
        days = _safe_int(entities.get("days", 7), 7)
        days = max(1, min(days, 31))
        return {
            "action": "list_events",
            "intent": intent,
            "provider": DEFAULT_PROVIDER,
            "days": days,
            "message": f"Here are your events for the next {days} days.",
        }

    # ---------- CALENDAR: CREATE EVENT ----------
    if intent == "create_event":
        raw = (entities.get("raw") or original_text or "").strip()
        timeframe = (entities.get("timeframe") or "").strip() or None
        duration_min = _safe_int(entities.get("duration_min", 30), 30)
        duration_min = max(5, min(duration_min, 240))

        title = _infer_event_title(raw)
        start_dt = _default_start_from_timeframe(timeframe, raw, DEFAULT_TZ)

        return {
            "action": "create_event",
            "intent": intent,
            "provider": DEFAULT_PROVIDER,
            "title": title,
            "start": start_dt.isoformat(),  # incluye offset
            "duration_min": duration_min,
            "message": "Creating your event.",
        }

    # ---------- MEETING ----------
    if intent == "meeting_scheduling":
        now = datetime.now()
        options = [
            {"label": "Option A", "start": (now + timedelta(days=1, hours=10)).isoformat(), "duration_min": 30},
            {"label": "Option B", "start": (now + timedelta(days=2, hours=14)).isoformat(), "duration_min": 30},
            {"label": "Option C", "start": (now + timedelta(days=3, hours=9)).isoformat(), "duration_min": 30},
        ]
        return {"action": "suggest_times", "intent": intent, "options": options}

    # ---------- EMAIL (placeholder) ----------
    if intent == "email_drafting":
        return {"action": "draft_email", "intent": intent, "message": "Email drafting flow planned (mock)."}

    # ---------- FOLLOW-UP (placeholder) ----------
    if intent == "follow_up_reminder":
        return {"action": "suggest_follow_up", "intent": intent, "message": "Follow-up flow planned (mock)."}

    # ---------- FALLBACK ----------
    return {"action": "unknown", "intent": "unknown", "message": "I’m not sure how to help with that yet."}
