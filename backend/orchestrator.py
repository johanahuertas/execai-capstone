# backend/orchestrator.py
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from zoneinfo import ZoneInfo

from .integrations import list_events_service, create_event_service


DEFAULT_PROVIDER = "google"
DEFAULT_TZ = "America/New_York"  # cámbialo si quieres o muévelo a env


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _infer_event_title(raw_text: str) -> str:
    """
    Heurística simple para demo.
    Si no encuentra nada claro, pone un título genérico.
    """
    t = (raw_text or "").strip()
    if not t:
        return "New Event"

    # Si escriben: add an event called "X"
    lowered = t.lower()
    if "called" in lowered:
        after = t.split("called", 1)[-1].strip().strip('"').strip("'")
        if after:
            return after[:80]

    return "ExecAI Event"


def _default_start_for_timeframe(timeframe: Optional[str], tz: ZoneInfo) -> datetime:
    """
    Si el usuario dice 'tomorrow' pero no da hora, usamos mañana a las 10:00am (demo-friendly).
    """
    now = datetime.now(tz)

    if timeframe == "tomorrow":
        d = (now + timedelta(days=1)).date()
        return datetime(d.year, d.month, d.day, 10, 0, tzinfo=tz)

    # fallback: hoy + 1 hora
    return now + timedelta(hours=1)


def handle_intent(intent_data: dict) -> dict:
    intent = intent_data.get("intent")
    entities: Dict[str, Any] = intent_data.get("entities") or {}

    # ---------- CALENDAR: LIST EVENTS ----------
    if intent == "list_events":
        days = _safe_int(entities.get("days", 7), 7)

        try:
            result = list_events_service(provider=DEFAULT_PROVIDER, days=days)
            return {
                "action": "list_events",
                "intent": intent,
                "message": f"Here are your events for the next {days} days.",
                "result": result,
            }
        except Exception as e:
            return {
                "action": "list_events",
                "intent": intent,
                "message": f"Could not fetch events. ({type(e).__name__})",
                "result": None,
            }

    # ---------- CALENDAR: CREATE EVENT ----------
    if intent == "create_event":
        raw = (entities.get("raw") or intent_data.get("original_text") or "").strip()
        timeframe = entities.get("timeframe")
        duration_min = _safe_int(entities.get("duration_min", 30), 30)

        tz = ZoneInfo(DEFAULT_TZ)

        # Si tu UI todavía no recoge fecha/hora exacta, usamos una default razonable
        start_dt = _default_start_for_timeframe(timeframe, tz)
        title = _infer_event_title(raw)

        try:
            result = create_event_service(
                provider=DEFAULT_PROVIDER,
                title=title,
                start=start_dt.isoformat(),  # IMPORTANT: incluye offset
                duration_min=duration_min,
            )
            return {
                "action": "create_event",
                "intent": intent,
                "message": "Event created.",
                "result": result,
            }
        except Exception as e:
            return {
                "action": "create_event",
                "intent": intent,
                "message": f"Could not create the event. ({type(e).__name__})",
                "result": None,
            }

    # ---------- MEETING ----------
    if intent == "meeting_scheduling":
        now = datetime.now()
        options = [
            {"label": "Option A", "start": (now + timedelta(days=1, hours=10)).isoformat(), "duration_min": 30},
            {"label": "Option B", "start": (now + timedelta(days=2, hours=14)).isoformat(), "duration_min": 30},
            {"label": "Option C", "start": (now + timedelta(days=3, hours=9)).isoformat(), "duration_min": 30},
        ]
        return {
            "action": "suggest_times",
            "intent": intent,
            "options": options,
        }

    # ---------- EMAIL (placeholder) ----------
    if intent == "email_drafting":
        return {
            "action": "draft_email",
            "intent": intent,
            "message": "Email drafting flow planned (mock).",
        }

    # ---------- FOLLOW-UP (placeholder) ----------
    if intent == "follow_up_reminder":
        return {
            "action": "suggest_follow_up",
            "intent": intent,
            "message": "Follow-up flow planned (mock).",
        }

    # ---------- FALLBACK ----------
    return {
        "action": "unknown",
        "intent": "unknown",
        "message": "I’m not sure how to help with that yet.",
    }
