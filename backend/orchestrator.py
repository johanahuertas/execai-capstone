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
from .ai_drafts import generate_email_draft, generate_reply_draft

DEFAULT_PROVIDER = "google"
DEFAULT_TZ = ZoneInfo("America/New_York")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []

    for item in items or []:
        val = str(item).strip()
        key = val.lower()

        if not val or key in seen:
            continue

        seen.add(key)
        out.append(val)

    return out


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

    natural_times = {
        "early morning": (7, 0),
        "in the morning": (9, 0),
        "morning": (9, 0),
        "mid-morning": (10, 0),
        "midmorning": (10, 0),
        "before lunch": (11, 0),
        "noon": (12, 0),
        "at noon": (12, 0),
        "lunchtime": (12, 0),
        "lunch time": (12, 0),
        "after lunch": (13, 0),
        "early afternoon": (13, 0),
        "afternoon": (14, 0),
        "in the afternoon": (14, 0),
        "mid-afternoon": (14, 30),
        "midafternoon": (14, 30),
        "late afternoon": (16, 0),
        "end of day": (16, 30),
    }

    for phrase, (hour, minute) in natural_times.items():
        if phrase in t:
            return hour, minute

    return None


def _default_start_from_timeframe(timeframe: Optional[str], raw_text: str, tz: ZoneInfo) -> datetime:
    now = datetime.now(tz)
    tf = (timeframe or "").strip()
    tf_lower = tf.lower()
    parsed_time = _parse_time_from_text(raw_text)

    iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", tf)
    if iso_match:
        y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
        hour, minute = parsed_time if parsed_time else (9, 0)
        return datetime(y, m, d, hour, minute, tzinfo=tz)

    iso_time_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})\s+at\s+(.+)$", tf, re.IGNORECASE)
    if iso_time_match:
        y, m, d = int(iso_time_match.group(1)), int(iso_time_match.group(2)), int(iso_time_match.group(3))
        time_part = _parse_time_from_text(iso_time_match.group(4))
        hour, minute = time_part if time_part else (9, 0)
        return datetime(y, m, d, hour, minute, tzinfo=tz)

    _month_abbr = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    natural_date = re.match(
        r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|"
        r"june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?"
        r"(?:\s+(\d{4}))?",
        tf_lower,
    )
    if natural_date:
        _m = _month_abbr.get(natural_date.group(1).lower(), 1)
        _d = int(natural_date.group(2))
        _y = int(natural_date.group(3)) if natural_date.group(3) else now.year
        hour, minute = parsed_time if parsed_time else (9, 0)
        return datetime(_y, _m, _d, hour, minute, tzinfo=tz)

    if tf_lower == "tomorrow":
        d = (now + timedelta(days=1)).date()
        hour, minute = parsed_time if parsed_time else (10, 0)
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)

    if tf_lower == "today":
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

    days_of_week = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if tf_lower in days_of_week:
        target_weekday = days_of_week.index(tf_lower)
        current_weekday = now.weekday()
        days_ahead = (target_weekday - current_weekday) % 7 or 7
        d = (now + timedelta(days=days_ahead)).date()
        hour, minute = parsed_time if parsed_time else (10, 0)
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)

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
    recipient = entities.get("recipient") or "there"
    subject = entities.get("subject")

    result = generate_email_draft(
        recipient=recipient,
        topic=topic,
        tone=tone,
        body_hint=body_hint,
        subject=subject,
    )

    return result.get("body", "")


def _build_reply_body(entities: Dict[str, Any]) -> str:
    tone = (entities.get("tone") or "neutral").lower()
    body_hint = (entities.get("body_hint") or "").strip()

    result = generate_reply_draft(
        tone=tone,
        body_hint=body_hint,
    )

    return result.get("body", "")


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
    try:
        same_day_start = datetime(start_dt.year, start_dt.month, start_dt.day, 8, 0, tzinfo=tz)
        same_day_end = datetime(start_dt.year, start_dt.month, start_dt.day, 18, 0, tzinfo=tz)

        now = datetime.now(tz)
        if same_day_start < now:
            search_start, search_end = timeframe_to_range(timeframe, tz)
        else:
            search_start = same_day_start
            search_end = same_day_end

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

        filtered: List[Dict[str, Any]] = []
        for slot in slots:
            slot_start_raw = slot.get("start")
            if not slot_start_raw:
                continue
            try:
                slot_start = datetime.fromisoformat(slot_start_raw)
            except Exception:
                continue
            if slot_start.date() == start_dt.date():
                filtered.append(_normalize_slot(slot))
            elif slot_start >= start_dt:
                filtered.append(_normalize_slot(slot))

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

    attendee_emails = _dedupe_keep_order(entities.get("attendee_emails", []) or [])
    attendee_names = _dedupe_keep_order(entities.get("attendee_names", []) or [])

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
        conflict_strs = [f"{c['title']} ({c['start']} – {c['end']})" for c in conflicts]

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
            "Conflict detected! You are busy during: "
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


def _build_meeting_scheduling_decision(intent: str, entities: Dict[str, Any], original_text: str) -> dict:
    timeframe = (entities.get("timeframe") or "").strip() or None
    duration_min = _safe_int(entities.get("duration_min", 30), 30)
    duration_min = max(5, min(duration_min, 240))

    attendee_emails = _dedupe_keep_order(entities.get("attendee_emails", []) or [])
    attendee_names = _dedupe_keep_order(entities.get("attendee_names", []) or [])

    explicit_title = (entities.get("title") or "").strip()
    raw = (original_text or "").strip()
    title = explicit_title or _infer_event_title(raw)

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
                "title": title,
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
            "title": title,
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
            "title": title,
            "source": "mock_availability",
            "busy_blocks": mock_busy,
            "busy_display": busy_display,
            "message": (
                "Google Calendar not connected — using simulated calendar. "
                f"Busy times: {', '.join(busy_display)}. "
                f"Found {len(slots)} available slot(s) that avoid conflicts."
            ),
        }


def _build_revise_draft_decision(intent: str, entities: Dict[str, Any]) -> Dict[str, Any]:
    revision_instruction = (entities.get("revision_instruction") or "").strip()
    tone = (entities.get("tone") or "professional").lower()

    return {
        "action": "revise_draft",
        "intent": intent,
        "provider": DEFAULT_PROVIDER,
        "revision_instruction": revision_instruction,
        "tone": tone,
        "message": "Revising your existing email draft.",
    }


def _build_revise_reply_draft_decision(intent: str, entities: Dict[str, Any]) -> Dict[str, Any]:
    revision_instruction = (entities.get("revision_instruction") or "").strip()
    tone = (entities.get("tone") or "neutral").lower()

    return {
        "action": "revise_reply_draft",
        "intent": intent,
        "provider": DEFAULT_PROVIDER,
        "revision_instruction": revision_instruction,
        "tone": tone,
        "message": "Revising your existing reply draft.",
    }


def handle_intent(intent_data: dict) -> dict:
    intent = (intent_data or {}).get("intent") or "unknown"
    entities: Dict[str, Any] = (intent_data or {}).get("entities") or {}
    original_text = (intent_data or {}).get("original_text") or ""

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

    if intent == "create_event":
        return _build_create_event_decision(intent, entities, original_text)

    if intent == "meeting_scheduling":
        return _build_meeting_scheduling_decision(intent, entities, original_text)

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

    if intent == "revise_reply_draft":
        return _build_revise_reply_draft_decision(intent, entities)

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
            "attendee_emails": event_decision.get("attendee_emails", []),
            "attendee_names": event_decision.get("attendee_names", []),
            "has_conflicts": event_decision.get("has_conflicts", False),
            "conflicts": event_decision.get("conflicts", []),
            "alternatives": event_decision.get("alternatives", []),
            "message": "Preparing a reply draft and calendar event.",
        }

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

    if intent == "revise_draft":
        return _build_revise_draft_decision(intent, entities)

    if intent == "draft_email_and_create_event":
        recipient = entities.get("recipient")
        subject = entities.get("subject") or entities.get("topic") or "Meeting"
        tone = entities.get("tone") or "professional"
        body = _build_draft_body(entities)

        event_decision = _build_create_event_decision(intent, entities, original_text)

        if not recipient:
            return {
                "action": "draft_email_and_create_event",
                "intent": intent,
                "provider": DEFAULT_PROVIDER,
                "missing": ["recipient"],
                "subject": subject,
                "body": body,
                "message": "I can prepare the draft and calendar event, but I need the recipient.",
            }

        return {
            "action": "draft_email_and_create_event",
            "intent": intent,
            "provider": DEFAULT_PROVIDER,
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "tone": tone,
            "event_title": event_decision.get("title"),
            "start": event_decision.get("start"),
            "duration_min": event_decision.get("duration_min"),
            "start_hint": event_decision.get("start_hint"),
            "attendee_emails": event_decision.get("attendee_emails", []),
            "attendee_names": event_decision.get("attendee_names", []),
            "has_conflicts": event_decision.get("has_conflicts", False),
            "conflicts": event_decision.get("conflicts", []),
            "alternatives": event_decision.get("alternatives", []),
            "message": "Preparing a Gmail draft and calendar event.",
        }

    if intent == "follow_up_reminder":
        return {
            "action": "suggest_follow_up",
            "intent": intent,
            "message": "Follow-up flow planned (mock).",
        }

    return {
        "action": "unknown",
        "intent": "unknown",
        "message": "I'm not sure how to help with that yet.",
    }