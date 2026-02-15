# backend/availability.py
# Smart slot-detection engine for ExecAI.

from __future__ import annotations

from datetime import datetime, timedelta, time
from typing import List, Dict, Any, Optional, Tuple
from zoneinfo import ZoneInfo

DEFAULT_TZ = ZoneInfo("America/New_York")

# Default working hours (9 AM – 5 PM)
DEFAULT_WORK_START = time(9, 0)
DEFAULT_WORK_END = time(17, 0)

# Slot granularity (check every 30 min)
SLOT_INCREMENT_MIN = 30

# Max suggestions to return
MAX_SUGGESTIONS = 3


# -------------------------------------------------------
# CORE: find available slots
# -------------------------------------------------------


def find_available_slots(
    busy_blocks: List[Dict[str, str]],
    search_start: datetime,
    search_end: datetime,
    duration_min: int = 30,
    work_start: time = DEFAULT_WORK_START,
    work_end: time = DEFAULT_WORK_END,
    tz: ZoneInfo = DEFAULT_TZ,
    max_results: int = MAX_SUGGESTIONS,
) -> List[Dict[str, Any]]:

    # Parse busy blocks into datetime pairs
    busy = _parse_busy_blocks(busy_blocks, tz)

    # Generate candidate slots
    candidates = _generate_candidates(
        search_start=search_start,
        search_end=search_end,
        duration_min=duration_min,
        work_start=work_start,
        work_end=work_end,
        tz=tz,
    )

    # Filter out candidates that overlap
    available = []
    for slot_start, slot_end in candidates:
        if not _overlaps_any(slot_start, slot_end, busy):
            available.append((slot_start, slot_end))

        if len(available) >= max_results:
            break

    # Format results
    results = []
    for i, (s, e) in enumerate(available):
        label = f"Option {chr(65 + i)}"
        results.append(
            {
                "label": label,
                "start": s.isoformat(),
                "end": e.isoformat(),
                "duration_min": duration_min,
                "display": _format_display(s, tz),
            }
        )

    return results


# -------------------------------------------------------
# HELPERS
# -------------------------------------------------------


def _parse_busy_blocks(
    blocks: List[Dict[str, str]], tz: ZoneInfo
) -> List[Tuple[datetime, datetime]]:
    """Parse ISO strings into (start, end) datetime pairs."""
    parsed = []
    for block in blocks:
        try:
            start = datetime.fromisoformat(block["start"])
            end = datetime.fromisoformat(block["end"])

            # Ensure timezone-aware datetimes
            if start.tzinfo is None:
                start = start.replace(tzinfo=tz)
            if end.tzinfo is None:
                end = end.replace(tzinfo=tz)

            parsed.append((start, end))
        except (KeyError, ValueError):
            continue  # Skip invalid blocks

    # Sort by start time
    parsed.sort(key=lambda x: x[0])
    return parsed


def _generate_candidates(
    search_start: datetime,
    search_end: datetime,
    duration_min: int,
    work_start: time,
    work_end: time,
    tz: ZoneInfo,
) -> List[Tuple[datetime, datetime]]:
    """
    Generate all possible meeting slots within working hours at SLOT_INCREMENT_MIN intervals.
    """
    candidates = []
    duration = timedelta(minutes=duration_min)
    increment = timedelta(minutes=SLOT_INCREMENT_MIN)

    # Ensure timezone-aware datetimes
    if search_start.tzinfo is None:
        search_start = search_start.replace(tzinfo=tz)
    if search_end.tzinfo is None:
        search_end = search_end.replace(tzinfo=tz)

    # Iterate day by day
    current_date = search_start.date()
    end_date = search_end.date()

    while current_date <= end_date:
        # Skip weekends
        if current_date.weekday() >= 5:
            current_date += timedelta(days=1)
            continue

        # Working hours for this day
        day_start = datetime.combine(current_date, work_start, tzinfo=tz)
        day_end = datetime.combine(current_date, work_end, tzinfo=tz)

        # Respect search bounds
        slot_start = max(day_start, search_start)
        day_limit = min(day_end, search_end)

        # Round slot_start up to next increment boundary
        slot_start = _round_up_to_increment(slot_start, SLOT_INCREMENT_MIN)

        while slot_start + duration <= day_limit:
            slot_end = slot_start + duration
            candidates.append((slot_start, slot_end))
            slot_start += increment

        current_date += timedelta(days=1)

    return candidates


def _round_up_to_increment(dt: datetime, increment_min: int) -> datetime:
    """Round a datetime UP to the nearest increment boundary."""
    minute = dt.minute
    remainder = minute % increment_min
    if remainder == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt
    add_min = increment_min - remainder
    rounded = dt.replace(second=0, microsecond=0) + timedelta(minutes=add_min)
    return rounded


def _overlaps_any(
    slot_start: datetime,
    slot_end: datetime,
    busy: List[Tuple[datetime, datetime]],
) -> bool:
    """Check if a candidate slot overlaps with ANY busy block."""
    for busy_start, busy_end in busy:
        # Two intervals overlap if one starts before the other ends
        if slot_start < busy_end and slot_end > busy_start:
            return True
    return False


def _format_display(dt: datetime, tz: ZoneInfo) -> str:
    """Human-readable display string for a slot time."""
    local = dt.astimezone(tz)
    return local.strftime("%a %b %d, %I:%M %p %Z")


# -------------------------------------------------------
# CONVENIENCE: build search range from timeframe
# -------------------------------------------------------


def timeframe_to_range(
    timeframe: Optional[str],
    tz: ZoneInfo = DEFAULT_TZ,
) -> Tuple[datetime, datetime]:
    """
    Convert a natural language timeframe into a (start, end) search range.
    """
    now = datetime.now(tz)
    tf = (timeframe or "").lower().strip()

    if tf == "today":
        start = now
        end = datetime.combine(now.date(), time(23, 59), tzinfo=tz)
        return start, end

    if tf == "tomorrow":
        tmr = (now + timedelta(days=1)).date()
        start = datetime.combine(tmr, time(0, 0), tzinfo=tz)
        end = datetime.combine(tmr, time(23, 59), tzinfo=tz)
        return start, end

    if tf == "this week":
        start = now
        days_until_friday = 4 - now.weekday()
        if days_until_friday < 0:
            days_until_friday = 0
        friday = (now + timedelta(days=days_until_friday)).date()
        end = datetime.combine(friday, time(23, 59), tzinfo=tz)
        return start, end

    if tf == "next week":
        days_until_monday = 7 - now.weekday()
        if now.weekday() == 0:
            days_until_monday = 7
        monday = (now + timedelta(days=days_until_monday)).date()
        friday = monday + timedelta(days=4)
        start = datetime.combine(monday, time(0, 0), tzinfo=tz)
        end = datetime.combine(friday, time(23, 59), tzinfo=tz)
        return start, end

    day_names = [
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday",
    ]
    if tf in day_names:
        target_weekday = day_names.index(tf)
        current_weekday = now.weekday()
        days_ahead = target_weekday - current_weekday
        if days_ahead <= 0:
            days_ahead += 7
        target_date = (now + timedelta(days=days_ahead)).date()
        start = datetime.combine(target_date, time(0, 0), tzinfo=tz)
        end = datetime.combine(target_date, time(23, 59), tzinfo=tz)
        return start, end

    # Default: next 3 business days
    start = now
    end = now + timedelta(days=5)
    return start, end


# -------------------------------------------------------
# MOCK BUSY DATA (used when Google is not connected)
# -------------------------------------------------------


def get_mock_busy_blocks(target_date: datetime, tz: ZoneInfo = DEFAULT_TZ) -> List[Dict[str, str]]:
    """
    Returns realistic mock busy blocks for a given date.
    Simulates a typical workday calendar.
    """
    d = target_date.date()

    # Skip weekends — no mock meetings
    if d.weekday() >= 5:
        return []

    blocks = [
        # Morning standup 9:00–9:30
        {
            "title": "Morning Standup",
            "start": datetime(d.year, d.month, d.day, 9, 0, tzinfo=tz).isoformat(),
            "end": datetime(d.year, d.month, d.day, 9, 30, tzinfo=tz).isoformat(),
        },
        # Team sync 10:00–11:00
        {
            "title": "Team Sync",
            "start": datetime(d.year, d.month, d.day, 10, 0, tzinfo=tz).isoformat(),
            "end": datetime(d.year, d.month, d.day, 11, 0, tzinfo=tz).isoformat(),
        },
        # Lunch 12:00–1:00
        {
            "title": "Lunch Break",
            "start": datetime(d.year, d.month, d.day, 12, 0, tzinfo=tz).isoformat(),
            "end": datetime(d.year, d.month, d.day, 13, 0, tzinfo=tz).isoformat(),
        },
        # 1-on-1 with manager 3:00–3:30
        {
            "title": "1-on-1 with Manager",
            "start": datetime(d.year, d.month, d.day, 15, 0, tzinfo=tz).isoformat(),
            "end": datetime(d.year, d.month, d.day, 15, 30, tzinfo=tz).isoformat(),
        },
    ]

    return blocks


# -------------------------------------------------------
# CONFLICT CHECKER
# -------------------------------------------------------


def check_conflicts(
    event_start: datetime,
    event_end: datetime,
    busy_blocks: List[Dict[str, str]],
    tz: ZoneInfo = DEFAULT_TZ,
) -> List[Dict[str, str]]:
    """
    Check if a proposed event time conflicts with any busy blocks.
    Returns a list of conflicting blocks.
    """
    parsed = _parse_busy_blocks(busy_blocks, tz)
    conflicts = []

    for busy_start, busy_end in parsed:
        if event_start < busy_end and event_end > busy_start:
            for block in busy_blocks:
                try:
                    bs = datetime.fromisoformat(block["start"])
                    if bs.tzinfo is None:
                        bs = bs.replace(tzinfo=tz)
                    if bs == busy_start:
                        conflicts.append({
                            "title": block.get("title", "Busy"),
                            "start": busy_start.strftime("%I:%M %p"),
                            "end": busy_end.strftime("%I:%M %p"),
                        })
                        break
                except (KeyError, ValueError):
                    continue
            else:
                conflicts.append({
                    "title": "Busy",
                    "start": busy_start.strftime("%I:%M %p"),
                    "end": busy_end.strftime("%I:%M %p"),
                })

    return conflicts


def get_busy_blocks(
    target_date: datetime,
    tz: ZoneInfo = DEFAULT_TZ,
    use_google: bool = False,
) -> List[Dict[str, str]]:
    """
    Get busy blocks for a target date.
    Uses Google FreeBusy if connected, otherwise returns mock data.
    """
    if use_google:
        try:
            from .integrations import get_freebusy_service

            day_start = datetime.combine(target_date.date(), time(0, 0), tzinfo=tz)
            day_end = datetime.combine(target_date.date(), time(23, 59), tzinfo=tz)

            result = get_freebusy_service(
                provider="google",
                time_min=day_start.isoformat(),
                time_max=day_end.isoformat(),
            )
            return result.get("busy_blocks", [])
        except Exception:
            pass

    return get_mock_busy_blocks(target_date, tz)