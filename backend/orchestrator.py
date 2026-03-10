# backend/orchestrator.py
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple, List

from zoneinfo import ZoneInfo

from .availability import (
    find_available_slots,
    timeframe_to_range,
    check_conflicts,
    get_busy_blocks,
    get_mock_busy_blocks,
)
from .integrations import get_freebusy_service

DEFAULT_PROVIDER = "google"
DEFAULT_TZ = ZoneInfo("America/New_York")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _infer_event_title(raw_text: str) -> str:
    t = (raw_text or "").strip()
    if not t:
        return "New Event"

    title = None

    m = re.search(r'called\s+"([^"]+)"', t, re.IGNORECASE)
    if m:
        title = m.group(1).strip()

    if not title:
        m2 = re.search(r"called\s+(.+)", t, re.IGNORECASE)
        if m2:
            title = m2.group(1).strip().strip('"').strip("'")

    if not title:
        return "ExecAI Event"

    title = re.sub(r"\b(tomorrow|today)\b\s*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\b(next week|this week)\b\s*$", "", title, flags=re.IGNORECASE).strip()
    title = title.strip(" -,:;")

    return title[:80] if title else "ExecAI Event"


def _parse_time_from_text(text: str) -> Optional[Tuple[int, int]]:
    t = (text or "").lower()

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

    m2 = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if m2:
        hour = int(m2.group(1))
        minute = int(m2.group(2))
        return hour, minute

    return None


def _default_start_from_timeframe(timeframe: Optional[str], raw_text: str, tz: ZoneInfo) -> datetime:
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
            if dt <= now:
                start = now + timedelta(hours=1)
                return start.replace(minute=0, second=0, microsecond=0)
            return dt

        start = now + timedelta(hours=1)
        return start.replace(minute=0, second=0, microsecond=0)

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


def _build_draft_body(entities: Dict[str, Any]) -> str:
    tone = (entities.get("tone") or "professional").lower()
    topic = entities.get("topic") or "your request"
    body_hint = entities.get("body_hint")

    if body_hint:
        return body_hint

    if tone == "friendly":
        return (
            f"Hi,\n\n"
            f"I hope you're doing well. I'm reaching out regarding {topic}. "
            f"Let me know the best next step.\n\n"
            f"Thanks so much,"
        )

    return (
        f"Hello,\n\n"
        f"I hope you are doing well. I am reaching out regarding {topic}. "
        f"Please let me know the best next step.\n\n"
        f"Best regards,"
    )


def _build_reply_body(entities: Dict[str, Any]) -> str:
    tone = (entities.get("tone") or "neutral").lower()
    body_hint = (entities.get("body_hint") or "").strip()

    if body_hint:
        if tone == "friendly":
            return f"Hi,\n\n{body_hint}\n\nThanks!"
        if tone == "professional":
            return f"Hello,\n\n{body_hint}\n\nBest regards,"
        return body_hint

    if tone == "friendly":
        return "Hi,\n\nThanks for the update.\n\nThanks!"
    if tone == "professional":
        return "Hello,\n\nThank you for the update.\n\nBest regards,"
    return "Thanks for the update."


def _normalize_slot(slot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "label": slot.get("label", "Option"),
        "start": slot.get("start"),
        "duration_min": slot.get("duration_min", 30),
    }


def _suggest_alternative_slots(
    timeframe: Optional[str],
    start_dt: datetime,
    duration_min: int,
    tz: ZoneInfo,
    max_options: int = 3,
) -> List[Dict[str, Any]]:
    """
    Suggest a few alternative free slots near the requested window.
    """
    try:
        search_start, search_end = timeframe_to_range(timeframe, tz)

        # Make sure the requested start is inside the search window.
        if start_dt < search_start:
            search_start = start_dt.replace(hour=8, minute=0, second=0, microsecond=0)
        if start_dt + timedelta(hours=8) > search_end:
            search_end = max(search_end, start_dt.replace(hour=18, minute=0, second=0, microsecond=0))

        try:
            freebusy_data = get_freebusy_service(
                provider="google",
                time_min=search_start.isoformat(),
                time_max=search_end.isoformat(),
            )
            busy_blocks = freebusy_data.get("busy_blocks", []) or []
        except Exception:
            busy_blocks = get_mock_busy_blocks(search_start, tz)

        slots = find_available_slots(
            busy_blocks=busy_blocks,
            search_start=search_start,
            search_end=search_end,
            duration_min=duration_min,
            tz=tz,
        ) or []

        # Prefer future options at or after the requested start
        filtered: List[Dict[str, Any]] = []
        for slot in slots:
            slot_start_raw = slot.get("start")
            if not slot_start_raw:
                continue
            try:
                slot_start = datetime.fromisoformat(slot_start_raw)
            except Exception:
                continue

            if slot_start >= start_dt:
                filtered.append(_normalize_slot(slot))

        # Fallback to earliest available if none after requested start
        if not filtered:
            filtered = [_normalize_slot(slot) for slot in slots[:max_options]]

        return filtered[:max_options]

    except Exception:
        return []


def _build_create_event_decision(intent: str, entities: Dict[str, Any], original_text: str) -> Dict[str, Any]:
    raw = (entities.get("raw") or original_text or "").strip()
    timeframe = (entities.get("timeframe") or "").strip() or None
    duration_min = _safe_int(entities.get("duration_min", 30), 30)
    duration_min = max(5, min(duration_min, 240))

    explicit_title = (entities.get("title") or "").strip()
    title = explicit_title or _infer_event_title(raw)

    start_source = (entities.get("start_hint") or raw or "").strip()
    start_dt = _default_start_from_timeframe(timeframe, start_source, DEFAULT_TZ)
    end_dt = start_dt + timedelta(minutes=duration_min)

    attendee_emails = entities.get("attendee_emails", [])
    attendee_names = entities.get("attendee_names", [])

    try:
        busy_blocks = get_busy_blocks(start_dt, DEFAULT_TZ, use_google=True)
    except Exception:
        busy_blocks = get_busy_blocks(start_dt, DEFAULT_TZ, use_google=False)

    conflicts = check_conflicts(start_dt, end_dt, busy_blocks, DEFAULT_TZ)

    result = {
        "action": "create_event",
        "intent": intent,
        "provider": DEFAULT_PROVIDER,
        "title": title,
        "start": start_dt.isoformat(),
        "duration_min": duration_min,
        "attendee_emails": attendee_emails,
        "attendee_names": attendee_names,
        "start_hint": entities.get("start_hint"),
    }

    if conflicts:
        conflict_strs = [
            f"{c['title']} ({c['start']} – {c['end']})" for c in conflicts
        ]
        alternatives = _suggest_alternative_slots(
            timeframe=timeframe,
            start_dt=start_dt,
            duration_min=duration_min,
            tz=DEFAULT_TZ,
            max_options=3,
        )

        result["conflicts"] = conflicts
        result["has_conflicts"] = True
        result["alternatives"] = alternatives
        result["message"] = (
            f"Conflict detected! You are busy during: "
            + ", ".join(conflict_strs)
            + "."
        )
        if alternatives:
            result["message"] += " I found alternative times you can use instead."
        else:
            result["message"] += " I could not find alternative times yet."
    else:
        result["has_conflicts"] = False
        result["alternatives"] = []
        result["message"] = "No conflicts found. Creating your event."

    return result


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
        return _build_create_event_decision(intent, entities, original_text)

    # ---------- EMAIL: LIST EMAILS ----------
    if intent == "list_emails":
        max_results = _safe_int(entities.get("max_results", 5), 5)
        max_results = max(1, min(max_results, 20))
        return {
            "action": "list_emails",
            "intent": intent,
            "provider": DEFAULT_PROVIDER,
            "max_results": max_results,
            "message": f"Here are your latest {max_results} emails.",
        }

    # ---------- EMAIL: READ EMAIL ----------
    if intent == "read_email":
        email_reference = entities.get("email_reference") or "latest"
        email_index = entities.get("email_index")

        return {
            "action": "read_email",
            "intent": intent,
            "provider": DEFAULT_PROVIDER,
            "email_reference": email_reference,
            "email_index": email_index,
            "message": "Opening the requested email.",
        }

    # ---------- EMAIL: REPLY TO EMAIL ----------
    if intent == "reply_email":
        email_reference = entities.get("email_reference") or "latest"
        email_index = entities.get("email_index")
        tone = entities.get("tone") or "neutral"
        body = _build_reply_body(entities)

        return {
            "action": "reply_email",
            "intent": intent,
            "provider": DEFAULT_PROVIDER,
            "email_reference": email_reference,
            "email_index": email_index,
            "body": body,
            "tone": tone,
            "message": "Preparing a reply draft for the requested email.",
        }

    # ---------- EMAIL + CALENDAR: REPLY AND CREATE EVENT ----------
    if intent == "reply_and_create_event":
        email_reference = entities.get("email_reference") or "latest"
        email_index = entities.get("email_index")
        tone = entities.get("tone") or "neutral"
        body = _build_reply_body(entities)

        event_decision = _build_create_event_decision(intent, entities, original_text)

        return {
            "action": "reply_and_create_event",
            "intent": intent,
            "provider": DEFAULT_PROVIDER,
            "email_reference": email_reference,
            "email_index": email_index,
            "body": body,
            "tone": tone,
            "event_title": event_decision.get("title"),
            "start": event_decision.get("start"),
            "duration_min": event_decision.get("duration_min"),
            "start_hint": event_decision.get("start_hint"),
            "has_conflicts": event_decision.get("has_conflicts", False),
            "conflicts": event_decision.get("conflicts", []),
            "alternatives": event_decision.get("alternatives", []),
            "message": "Preparing a reply draft and calendar event.",
        }

    # ---------- MEETING ----------
    if intent == "meeting_scheduling":
        timeframe = (entities.get("timeframe") or "").strip() or None
        duration_min = _safe_int(entities.get("duration_min", 30), 30)
        duration_min = max(5, min(duration_min, 240))
        attendee_emails = entities.get("attendee_emails", [])
        attendee_names = entities.get("attendee_names", [])

        try:
            search_start, search_end = timeframe_to_range(timeframe, DEFAULT_TZ)

            freebusy_data = get_freebusy_service(
                provider="google",
                time_min=search_start.isoformat(),
                time_max=search_end.isoformat(),
            )

            busy_blocks = freebusy_data.get("busy_blocks", [])

            slots = find_available_slots(
                busy_blocks=busy_blocks,
                search_start=search_start,
                search_end=search_end,
                duration_min=duration_min,
                tz=DEFAULT_TZ,
            )

            if slots:
                return {
                    "action": "suggest_times",
                    "intent": intent,
                    "provider": "google",
                    "options": slots,
                    "attendee_emails": attendee_emails,
                    "attendee_names": attendee_names,
                    "duration_min": duration_min,
                    "source": "real_availability",
                    "message": f"Found {len(slots)} available slot(s) based on your calendar.",
                }

            return {
                "action": "suggest_times",
                "intent": intent,
                "provider": "google",
                "options": [],
                "attendee_emails": attendee_emails,
                "attendee_names": attendee_names,
                "duration_min": duration_min,
                "source": "real_availability",
                "message": "No available slots found in that timeframe. Try a different day or time range.",
            }

        except Exception:
            search_start, search_end = timeframe_to_range(timeframe, DEFAULT_TZ)
            mock_busy = get_mock_busy_blocks(search_start, DEFAULT_TZ)

            slots = find_available_slots(
                busy_blocks=mock_busy,
                search_start=search_start,
                search_end=search_end,
                duration_min=duration_min,
                tz=DEFAULT_TZ,
            )

            busy_display = [
                f"{b.get('title', 'Busy')} ({datetime.fromisoformat(b['start']).strftime('%I:%M %p')}–{datetime.fromisoformat(b['end']).strftime('%I:%M %p')})"
                for b in mock_busy
            ]

            return {
                "action": "suggest_times",
                "intent": intent,
                "provider": "mock",
                "options": slots,
                "attendee_emails": attendee_emails,
                "attendee_names": attendee_names,
                "duration_min": duration_min,
                "source": "mock_availability",
                "busy_blocks": mock_busy,
                "busy_display": busy_display,
                "message": (
                    "Google Calendar not connected — using simulated calendar. "
                    f"Busy times: {', '.join(busy_display)}. "
                    f"Found {len(slots)} available slot(s) that avoid conflicts."
                ),
            }

    # ---------- EMAIL: CREATE DRAFT ----------
    if intent == "email_drafting":
        recipient = entities.get("recipient")
        subject = entities.get("subject") or entities.get("topic") or "Quick Follow-Up"
        tone = entities.get("tone") or "professional"

        if not recipient:
            return {
                "action": "create_draft",
                "intent": intent,
                "provider": DEFAULT_PROVIDER,
                "missing": ["recipient"],
                "message": "I can create the Gmail draft, but I need the recipient.",
            }

        body = _build_draft_body(entities)

        return {
            "action": "create_draft",
            "intent": intent,
            "provider": DEFAULT_PROVIDER,
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "tone": tone,
            "message": "Creating Gmail draft.",
        }

    # ---------- FOLLOW-UP ----------
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