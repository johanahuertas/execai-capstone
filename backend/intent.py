import os
import re
import json
from typing import Any, Dict, Optional, List

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

USE_LLM = bool(os.getenv("OPENAI_API_KEY") or os.getenv("GROQ_API_KEY"))

_client = None
_DEFAULT_MODEL = "gpt-5.4-nano"

if USE_LLM:
    try:
        from openai import OpenAI

        if os.getenv("OPENAI_API_KEY"):
            _client = OpenAI(
                api_key=os.getenv("OPENAI_API_KEY"),
            )
            _DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-nano")
        elif os.getenv("GROQ_API_KEY"):
            _client = OpenAI(
                api_key=os.getenv("GROQ_API_KEY"),
                base_url="https://api.groq.com/openai/v1",
            )
            _DEFAULT_MODEL = os.getenv("AI_MODEL", "llama-3.3-70b-versatile")
        else:
            _client = None
            USE_LLM = False
    except Exception:
        _client = None
        USE_LLM = False

EMAIL_REGEX = r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"

_STOP_WORDS = {
    "the", "my", "a", "an", "this", "that", "it", "me", "them", "us",
    "his", "her", "its", "our", "their", "your", "some", "any", "all",
    "to", "in", "on", "at", "of", "by", "up", "so", "if", "or", "as",
    "is", "am", "are", "was", "be", "do", "no", "not", "yes",
    "most", "recent", "latest", "last", "first", "new", "old",
    "tomorrow", "today", "next", "week", "month", "calendar",
    "email", "emails", "inbox", "draft", "reply", "respond", "send",
    "about", "regarding", "for", "with", "from", "and", "but",
    "please", "could", "would", "should", "can", "will", "just",
    "meeting", "event", "appointment", "schedule",
    "team", "group", "everyone", "somebody", "someone", "nobody",
    "here", "there", "then", "now", "back", "again",
    "saying", "telling", "asking", "writing", "drafting",
    "professional", "friendly", "casual", "formal",
    "propose", "proposal", "proposa",
}

_REVISION_PHRASES_EXACT = {
    "shorter",
    "longer",
    "more formal",
    "less formal",
    "more casual",
    "more professional",
    "friendlier",
    "warmer",
    "better",
    "rewrite it",
    "revise it",
    "fix it",
    "make it better",
    "make it shorter",
    "make it longer",
    "one line",
    "one sentence",
    "too long",
    "less cheesy",
    "more direct",
    "clean this up",
}

_REVISION_KEYWORDS = [
    "shorter", "longer", "formal", "casual", "professional", "friendly",
    "friendlier", "warmer", "rewrite", "revise", "reword", "fix",
    "improve", "better", "clean up", "make it", "change it",
    "add ", "remove ", "mention ", "say ", "replace ",
    "one line", "one sentence", "too long", "direct", "softer",
    "simpler", "clean", "less cheesy", "less wordy",
]


# -----------------------
# EXTRACTION HELPERS
# -----------------------

def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items or []:
        value = str(item).strip()
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _extract_attendee_emails(text: str) -> List[str]:
    t = (text or "").strip()
    emails = re.findall(EMAIL_REGEX, t)
    return _dedupe_keep_order([e.lower() for e in emails])


def _extract_attendee_names(text: str) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []
    names: List[str] = []
    m = re.search(r"\bwith\s+(.+)", t, re.IGNORECASE)
    if m:
        names.extend(_parse_name_chunk(m.group(1)))
    if not names:
        m2 = re.search(r"\binvite\s+(.+)", t, re.IGNORECASE)
        if m2:
            names.extend(_parse_name_chunk(m2.group(1)))
    if not names:
        m3 = re.search(r"\bbetween\s+(.+)", t, re.IGNORECASE)
        if m3:
            names.extend(_parse_name_chunk(m3.group(1)))
    return _dedupe_keep_order([n.lower() for n in names])


def _parse_name_chunk(chunk: str) -> List[str]:
    boundary = re.split(
        r"\b(?:tomorrow|today|next|this|at|on|for|about|regarding|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"\d{1,2}(?::\d{2})?\s*(?:am|pm)|in the|after|before)\b",
        chunk, maxsplit=1, flags=re.IGNORECASE,
    )[0].strip()
    if not boundary:
        return []
    boundary = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "", boundary)
    parts = re.split(r"[,]?\s+and\s+|,\s*", boundary)
    names = []
    for p in parts:
        p = p.strip().strip(".,;:'\"")
        if not p or len(p) < 2:
            continue
        words = p.split()
        if len(words) > 3:
            continue
        if words[0][0].isupper() or words[0].lower() in {"dr.", "mr.", "ms.", "mrs.", "prof."}:
            names.append(p)
    return names


def _extract_participants(text: str) -> Optional[int]:
    t = (text or "").lower()
    if "all four of us" in t or "the four of us" in t:
        return 4
    if "all three of us" in t or "the three of us" in t:
        return 3
    if "both of us" in t or "the two of us" in t:
        return 2
    m_people = re.search(r"\b(\d+)\s+(people|persons|attendees|guests|participants)\b", t)
    if m_people:
        try:
            n = int(m_people.group(1))
            if 1 <= n <= 50:
                return n
        except ValueError:
            pass
    word_map = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10
    }
    for w, n in word_map.items():
        if re.search(rf"\b{w}\s+(people|persons|attendees|guests|participants)\b", t):
            return n
    return None


def _extract_timeframe(text: str) -> Optional[str]:
    t = (text or "").lower()
    if "next week" in t:
        return "next week"
    if "this week" in t:
        return "this week"
    if "tomorrow" in t:
        return "tomorrow"
    if "today" in t:
        return "today"

    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for d in days:
        if d in t:
            return d

    if "next month" in t:
        return "next month"
    if "this month" in t:
        return "this month"

    _month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    _date_match = re.search(
        r"\b(january|february|march|april|may|june|july|august"
        r"|september|october|november|december)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?\b",
        t, re.IGNORECASE,
    )
    if _date_match:
        from datetime import date as _date
        _month = _month_map[_date_match.group(1).lower()]
        _day = int(_date_match.group(2))
        _year = int(_date_match.group(3)) if _date_match.group(3) else _date.today().year
        return f"{_year}-{_month:02d}-{_day:02d}"
    return None


def _extract_meeting_type(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in ["zoom", "teams", "call", "phone call", "video call"]):
        return "call"
    if any(w in t for w in ["meeting", "meet", "sync", "catch up", "check-in"]):
        return "meeting"
    return "unknown"


def _extract_duration_min(text: str) -> Optional[int]:
    t = (text or "").lower()
    m = re.search(r"\b(\d{1,3})\s*(min|mins|minute|minutes)\b", t)
    if m:
        n = int(m.group(1))
        if 5 <= n <= 240:
            return n

    m2 = re.search(r"\bfor\s+(\d{1,3})\s*(min|mins|minute|minutes)\b", t)
    if m2:
        n = int(m2.group(1))
        if 5 <= n <= 240:
            return n

    m3 = re.search(r"\b(\d{1,2})\s*(hour|hours|hr|hrs)\b", t)
    if m3:
        n = int(m3.group(1)) * 60
        if 5 <= n <= 240:
            return n

    if "half an hour" in t:
        return 30
    if "an hour" in t or "1 hour" in t:
        return 60
    return None


def _extract_tone(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in ["professional", "formally", "formal"]):
        return "professional"
    if any(w in t for w in ["friendly", "casual"]):
        return "friendly"
    return "neutral"


def _extract_recipient(text: str) -> Optional[str]:
    t = (text or "").strip()

    email_match = re.search(EMAIL_REGEX, t)
    if email_match:
        return email_match.group(0).lower()

    m = re.search(r"\bemail\s+(?:to\s+)?([A-Za-z][a-z]{1,})\b", t)
    if m:
        name = m.group(1).lower()
        if name not in _STOP_WORDS and len(name) > 1:
            return name

    drafting_context = any(w in t.lower() for w in [
        "draft", "write", "compose", "send", "message to",
        "reach out to", "shoot", "need to send",
    ])
    if drafting_context:
        m2 = re.search(r"\bto\s+([A-Z][a-z]{1,})\b", t)
        if m2:
            name = m2.group(1).lower()
            if name not in _STOP_WORDS:
                return name
        m3 = re.search(r"\bto\s+([a-z]{2,})\b", t.lower())
        if m3:
            name = m3.group(1)
            if name not in _STOP_WORDS:
                return name

    return None


def _extract_topic(text: str) -> Optional[str]:
    t = (text or "").lower()
    for topic in [
        "invoice", "contract", "proposal", "meeting", "payment",
        "follow-up", "reminder", "schedule", "availability", "budget",
        "review", "update",
    ]:
        if topic in t:
            return topic
    return None


def _extract_email_subject(text: str) -> Optional[str]:
    t = (text or "").strip()

    m = re.search(r'subject\s+"([^"]+)"', t, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    m2 = re.search(r"\babout\s+(.+)", t, re.IGNORECASE)
    if m2:
        candidate = m2.group(1).strip().strip('"').strip("'")
        candidate = re.split(
            r"\bsaying\b|\bthat says\b|\band create\b|\band schedule\b|\band send\b",
            candidate,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()
        if candidate:
            return candidate[:120]

    return None


def _extract_email_body_hint(text: str) -> Optional[str]:
    t = (text or "").strip()

    m = re.search(r'saying\s+"([^"]+)"', t, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    m2 = re.search(r'say\s+"([^"]+)"', t, re.IGNORECASE)
    if m2:
        return m2.group(1).strip()

    m3 = re.search(r'reply(?:ing)?\s+(?:with|saying)\s+"([^"]+)"', t, re.IGNORECASE)
    if m3:
        return m3.group(1).strip()

    m4 = re.search(
        r'\bdraft an email .*?\bsaying\s+(.+?)(?:\s+and\s+create|\s+and\s+schedule|$)',
        t,
        re.IGNORECASE,
    )
    if m4:
        return m4.group(1).strip().strip('"').strip("'")

    m5 = re.search(
        r'\babout\s+.+?\bsaying\s+(.+?)(?:\s+and\s+create|\s+and\s+schedule|$)',
        t,
        re.IGNORECASE,
    )
    if m5:
        return m5.group(1).strip().strip('"').strip("'")

    return None


def _extract_email_reference(text: str) -> Optional[str]:
    t = (text or "").lower()
    if any(p in t for p in [
        "latest email", "most recent email", "last email",
        "email i received", "email i got", "sent to me",
        "that email", "the email"
    ]):
        return "latest"
    if "first email" in t:
        return "first"
    m = re.search(r"\bemail\s+(\d+)\b", t)
    if m:
        return "indexed"
    return None


def _extract_email_index(text: str) -> Optional[int]:
    t = (text or "").lower()
    if "first email" in t:
        return 1
    m = re.search(r"\bemail\s+(\d+)\b", t)
    if m:
        try:
            n = int(m.group(1))
            if n >= 1:
                return n
        except ValueError:
            pass
    return None


def _extract_time_string(text: str) -> Optional[str]:
    t = (text or "").lower()
    m = re.search(r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", t, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", t, re.IGNORECASE)
    if m2:
        return m2.group(1).strip()
    m3 = re.search(r"\bat\s+([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if m3:
        return f"{m3.group(1)}:{m3.group(2)}"
    return None


def _extract_event_title(text: str) -> Optional[str]:
    t = (text or "").strip()
    m = re.search(r'called\s+"([^"]+)"', t, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    m2 = re.search(r"called\s+(.+)", t, re.IGNORECASE)
    if m2:
        candidate = m2.group(1).strip().strip('"').strip("'")
        candidate = re.split(
            r"\b(tomorrow|today|next week|this week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            candidate, maxsplit=1, flags=re.IGNORECASE,
        )[0].strip(" ,.-")
        if candidate:
            return candidate[:120]

    m3 = re.search(
        r"\b(?:create|schedule|add|book|make)\s+(?:an?\s+)?(.+)",
        t, re.IGNORECASE,
    )
    if m3:
        candidate = m3.group(1).strip().strip('"').strip("'")
        candidate = re.sub(r"^(event|meeting|appointment|name)\b\s*", "", candidate, flags=re.IGNORECASE).strip()
        candidate = re.split(r"\bwith\b", candidate, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        candidate = re.split(
            r"\b(on\s+(?:january|february|march|april|may|june|july|august|"
            r"september|october|november|december)|"
            r"tomorrow|today|next week|this week|monday|tuesday|wednesday|"
            r"thursday|friday|saturday|sunday|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)|"
            r"for\s+\d+\s*(?:min|mins|minute|minutes|hour|hours)|"
            r"(?:january|february|march|april|may|june|july|august|"
            r"september|october|november|december)\s+\d{1,2})\b",
            candidate, maxsplit=1, flags=re.IGNORECASE,
        )[0].strip(" ,.-")
        candidate = re.sub(r"\s+for\s*$", "", candidate, flags=re.IGNORECASE).strip(" ,.-")
        candidate = re.sub(r"^(the|a|an)\s+", "", candidate, flags=re.IGNORECASE).strip()
        generic_titles = {"meeting": "Meeting", "event": "Event", "appointment": "Appointment"}
        normalized = candidate.lower().strip()
        if normalized in generic_titles:
            return generic_titles[normalized]
        if candidate and len(candidate) > 1:
            return candidate[:120]

    if re.search(r"\bbudget review\b", t, re.IGNORECASE):
        return "budget review"
    if re.search(r"\bmeeting\b", t, re.IGNORECASE):
        return "Meeting"
    if re.search(r"\bevent\b", t, re.IGNORECASE):
        return "Event"
    if re.search(r"\bappointment\b", t, re.IGNORECASE):
        return "Appointment"
    return None


def _extract_start_hint(text: str) -> Optional[str]:
    timeframe = _extract_timeframe(text)
    time_str = _extract_time_string(text)
    if timeframe and time_str:
        return f"{timeframe} at {time_str}"
    if timeframe:
        return timeframe
    if time_str:
        return time_str
    return None


# -----------------------
# CONTEXT FOLLOW-UP HELPERS
# -----------------------

def _looks_like_revision_followup(text: str, last_context: Optional[Dict[str, Any]] = None) -> Optional[str]:
    t = (text or "").strip().lower()
    if not t or not last_context:
        return None

    last_action = (last_context.get("action") or "").strip().lower()

    allowed_last_actions_for_draft = {
        "create_draft",
        "draft_email",
        "draft_email_and_create_event",
    }
    allowed_last_actions_for_reply = {
        "reply_email",
        "reply_and_create_event",
    }

    if last_action not in allowed_last_actions_for_draft and last_action not in allowed_last_actions_for_reply:
        return None

    if _looks_like_reply_and_create_event(t):
        return None
    if _looks_like_draft_and_create_event(t):
        return None
    if _looks_like_list_events(t):
        return None
    if _looks_like_reply_email(t):
        return None
    if _looks_like_read_email(t):
        return None
    if _looks_like_list_emails(t):
        return None
    if _looks_like_email_drafting(t):
        return None
    if _looks_like_suggest_times(t):
        return None
    if _looks_like_create_event(t):
        return None

    strong_new_task_words = [
        "create event", "schedule meeting", "list emails", "show inbox",
        "read email", "open email", "reply to", "draft an email",
        "write an email", "compose an email", "send an email",
    ]
    if any(p in t for p in strong_new_task_words):
        return None

    if last_action in allowed_last_actions_for_reply:
        return "revise_reply_draft"

    return "revise_draft"


# -----------------------
# CALENDAR HELPERS
# -----------------------

def _has_word(t: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", t) is not None


def _looks_like_list_events(text: str) -> bool:
    t = (text or "").lower()
    explicit_phrases = [
        "what does my schedule look like", "what's on my calendar",
        "what is on my calendar", "show my schedule", "show my agenda",
        "what do i have", "my week look like", "my day look like",
    ]
    if any(p in t for p in explicit_phrases):
        return True
    calendar_words = ["calendar", "events", "event", "agenda", "schedule"]
    list_words = ["list", "show", "see", "get", "view", "whats", "what's"]
    has_calendar = any(_has_word(t, w) for w in calendar_words)
    has_list = any(_has_word(t, v) for v in list_words)
    return has_calendar and has_list


def _looks_like_suggest_times(text: str) -> bool:
    t = (text or "").lower()
    explicit_phrases = [
        "find a time", "find time", "suggest a time", "suggest times",
        "when are we free", "when am i free", "when are you free",
        "what time works", "what times work", "help me schedule", "availability for",
    ]
    if any(p in t for p in explicit_phrases):
        return True
    create_words = ["create", "add", "book", "make", "schedule", "put"]
    has_create_action = any(_has_word(t, w) for w in create_words)
    if has_create_action and any(_has_word(t, w) for w in ["meeting", "event", "appointment"]):
        return False
    has_schedule_word = any(_has_word(t, w) for w in ["meet", "meeting", "schedule", "availability", "free"])
    has_timeframe = bool(_extract_timeframe(t))
    has_time = bool(_extract_time_string(t))
    return has_schedule_word and has_timeframe and not has_time


def _extract_days(text: str) -> int:
    t = (text or "").lower()
    m = re.search(r"\b(?:next|for)\s+(\d+)\s*day", t)
    if m:
        try:
            n = int(m.group(1))
            return max(1, min(n, 31))
        except ValueError:
            pass
    if _has_word(t, "tomorrow") or _has_word(t, "today"):
        return 1
    if "next week" in t:
        return 7
    return 7


def _looks_like_create_event(text: str) -> bool:
    t = (text or "").lower()
    create_phrases = [
        "create event", "create an event", "create a calendar event",
        "add event", "add an event", "schedule event", "schedule an event",
        "book event", "book an event", "add to my calendar", "put on my calendar",
        "create a meeting", "create meeting",
        "schedule a meeting", "book a meeting", "add a meeting",
        "make a meeting", "create an appointment", "schedule an appointment",
        "set up a meeting", "set up a call", "set up a zoom",
        "set up an event", "set up a standup", "set up a sync",
        "put a meeting", "put an event", "put a call",
        "block time", "block off time",
    ]
    if any(p in t for p in create_phrases):
        return True
    has_action = any(_has_word(t, w) for w in ["create", "add", "schedule", "book", "make", "put"])
    has_time_context = bool(_extract_timeframe(t) or _extract_time_string(t))
    has_explicit_time = bool(_extract_time_string(t))
    has_attendee = bool(_extract_attendee_emails(t))
    has_called = _has_word(t, "called")
    has_calendar_word = any(_has_word(t, w) for w in ["event", "meeting", "appointment", "calendar"])
    return has_action and (
        has_explicit_time
        or (has_calendar_word and has_time_context)
        or has_called
        or (has_attendee and has_explicit_time)
    )


# -----------------------
# EMAIL HELPERS
# -----------------------

def _looks_like_list_emails(text: str) -> bool:
    t = (text or "").lower()
    phrases = [
        "list my emails", "show my emails", "show my latest emails",
        "show my recent emails", "list my recent emails", "check my inbox",
        "show my inbox", "read my emails", "list my latest emails",
        "show latest emails", "show recent emails", "read my inbox",
    ]
    return any(phrase in t for phrase in phrases)


def _looks_like_read_email(text: str) -> bool:
    t = (text or "").lower()
    phrases = [
        "read my latest email", "open my latest email", "read the latest email",
        "open the latest email", "read my most recent email",
        "open my most recent email", "read the first email", "open the first email",
        "open the last email", "read the last email",
        "open last email", "read last email",
        "the last email i got", "the last email i received",
        "last email i got", "last email i received",
        "show me the email", "open that email",
        "read the most recent email", "open the most recent email",
        "show me my latest email", "show me the latest email",
        "show the last email", "show my last email",
        "what did my last email say",
    ]
    if any(phrase in t for phrase in phrases):
        return True
    if re.search(r"\b(open|read|show)\s+(my\s+|the\s+)?(latest|last|most recent|recent)\s*email\b", t):
        return True
    if re.search(r"\b(open|read)\s+email\s+\d+\b", t):
        return True
    return False


def _looks_like_reply_and_create_event(text: str) -> bool:
    t = (text or "").lower()
    has_reply = bool(re.search(
        r"\b(reply|respond)\s+(to\s+)?(my\s+|the\s+|that\s+)?(latest|last|most recent|recent)?\s*email\b", t
    ))
    has_create_event = (
        "create the meeting" in t or "create a meeting" in t
        or "create an event" in t or "schedule the meeting" in t
        or "schedule a meeting" in t or "create the event" in t
        or "and create" in t or "and schedule" in t
    )
    has_time_context = bool(_extract_timeframe(t) or _extract_time_string(t))
    return has_reply and has_create_event and has_time_context


def _looks_like_draft_and_create_event(text: str) -> bool:
    t = (text or "").lower()
    has_draft = _looks_like_email_drafting(t) or any(
        phrase in t for phrase in ["email ", "write to ", "draft to "]
    )
    has_create_event = (
        "create the meeting" in t or "create a meeting" in t
        or "create an event" in t or "schedule the meeting" in t
        or "schedule a meeting" in t or "create the event" in t
        or "and create" in t or "and schedule" in t
    )
    has_time_context = bool(_extract_timeframe(t) or _extract_time_string(t))
    has_recipient = bool(_extract_recipient(t))
    return has_draft and has_create_event and has_time_context and has_recipient


def _looks_like_reply_email(text: str) -> bool:
    t = (text or "").lower()
    phrases = [
        "reply to my latest email", "reply to the latest email",
        "reply to my most recent email", "reply to the most recent email",
        "reply to the first email",
        "respond to my latest email", "respond to the latest email",
        "respond to my most recent email", "respond to the most recent email",
        "reply to the last email", "reply to my last email",
        "respond to the last email", "respond to my last email",
        "respond to the email", "reply to the email",
        "reply to that email", "respond to that email",
        "most recent email i have received",
        "most recent email that was sent to me",
        "latest email i received", "latest email i got",
        "last email i received", "last email i got",
        "email that was sent to me",
    ]
    if any(phrase in t for phrase in phrases):
        return True
    if re.search(r"\b(reply|respond)\s+(to\s+)?(my\s+|the\s+|that\s+)?(latest|last|most recent|recent)?\s*email\b", t):
        return True
    if re.search(r"\b(reply|respond)\s+to\s+email\s+\d+\b", t):
        return True
    return False


def _looks_like_email_drafting(text: str) -> bool:
    t = (text or "").lower()
    drafting_phrases = [
        "draft an email", "draft email", "write an email", "write email",
        "compose an email", "compose email", "create a draft", "create draft",
        "send an email", "send a message", "send message",
        "shoot an email", "shoot a message",
        "need to send", "i need to send",
        "reach out to", "message to",
    ]
    if any(p in t for p in drafting_phrases):
        return True
    if re.search(r"\bsend\s+(?:her|him|them|me)\s+(?:an?\s+)?email\b", t):
        return True
    if re.search(r"\bemail\s+(?:her|him|them)\b", t):
        return True
    return False


# -----------------------
# RULE-BASED PARSER
# -----------------------

def _classify_intent_rules(text: str, last_context: Optional[Dict[str, Any]] = None) -> str:
    t = (text or "").lower()

    followup_intent = _looks_like_revision_followup(text, last_context)
    if followup_intent:
        return followup_intent

    if _looks_like_reply_and_create_event(t):
        return "reply_and_create_event"
    if _looks_like_draft_and_create_event(t):
        return "draft_email_and_create_event"
    if _looks_like_list_events(t):
        return "list_events"

    if _looks_like_reply_email(t):
        return "reply_email"

    if _looks_like_read_email(t):
        return "read_email"
    if _looks_like_list_emails(t):
        return "list_emails"
    if _looks_like_email_drafting(t):
        return "email_drafting"
    if any(w in t for w in ["follow up", "follow-up", "remind", "reminder"]):
        return "follow_up_reminder"
    if _looks_like_suggest_times(t):
        return "meeting_scheduling"
    if _looks_like_create_event(t):
        return "create_event"

    if re.search(r"\b(reply|respond)\b", t):
        return "reply_email"

    if any(w in t for w in ["send an email", "email", "write an email"]):
        return "email_drafting"
    if any(w in t for w in ["schedule", "find a time", "set up a meeting", "book a meeting", "meet"]):
        return "meeting_scheduling"

    return "unknown"


def _parse_intent_rules(text: str, last_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    intent = _classify_intent_rules(text, last_context)
    entities: Dict[str, Any] = {}

    if intent == "list_events":
        entities["days"] = _extract_days(text)

    elif intent == "create_event":
        entities["raw"] = text
        entities["title"] = _extract_event_title(text)
        entities["timeframe"] = _extract_timeframe(text)
        entities["start_hint"] = _extract_start_hint(text)
        entities["duration_min"] = _extract_duration_min(text) or 30
        entities["attendee_emails"] = _extract_attendee_emails(text)
        entities["attendee_names"] = _extract_attendee_names(text)

    elif intent == "reply_and_create_event":
        entities["email_reference"] = _extract_email_reference(text) or "latest"
        entities["email_index"] = _extract_email_index(text)
        entities["body_hint"] = _extract_email_body_hint(text)
        entities["tone"] = _extract_tone(text)
        entities["title"] = _extract_event_title(text) or "Meeting"
        entities["timeframe"] = _extract_timeframe(text)
        entities["start_hint"] = _extract_start_hint(text)
        entities["duration_min"] = _extract_duration_min(text) or 30
        entities["attendee_emails"] = _extract_attendee_emails(text)
        entities["attendee_names"] = _extract_attendee_names(text)

    elif intent == "draft_email_and_create_event":
        entities["recipient"] = _extract_recipient(text)
        entities["topic"] = _extract_topic(text)
        entities["tone"] = _extract_tone(text)
        entities["subject"] = _extract_email_subject(text) or _extract_topic(text) or "Meeting"
        entities["body_hint"] = _extract_email_body_hint(text)
        entities["title"] = _extract_event_title(text) or "Meeting"
        entities["timeframe"] = _extract_timeframe(text)
        entities["start_hint"] = _extract_start_hint(text)
        entities["duration_min"] = _extract_duration_min(text) or 30
        entities["attendee_emails"] = _extract_attendee_emails(text)
        entities["attendee_names"] = _extract_attendee_names(text)
        recipient = entities.get("recipient")
        if recipient and recipient not in entities["attendee_emails"]:
            entities["attendee_emails"] = _dedupe_keep_order(entities["attendee_emails"] + [recipient])

    elif intent == "reply_email":
        entities["email_reference"] = _extract_email_reference(text) or "latest"
        entities["email_index"] = _extract_email_index(text)
        entities["body_hint"] = _extract_email_body_hint(text)
        entities["tone"] = _extract_tone(text)

    elif intent == "read_email":
        entities["email_reference"] = _extract_email_reference(text) or "latest"
        entities["email_index"] = _extract_email_index(text)

    elif intent == "list_emails":
        entities["max_results"] = 5

    elif intent == "meeting_scheduling":
        entities["participants"] = _extract_participants(text)
        entities["timeframe"] = _extract_timeframe(text)
        entities["meeting_type"] = _extract_meeting_type(text)
        entities["duration_min"] = _extract_duration_min(text) or 30
        entities["attendee_emails"] = _extract_attendee_emails(text)
        entities["attendee_names"] = _extract_attendee_names(text)
        entities["title"] = _extract_event_title(text)

    elif intent == "email_drafting":
        entities["recipient"] = _extract_recipient(text)
        entities["topic"] = _extract_topic(text)
        entities["tone"] = _extract_tone(text)
        entities["subject"] = _extract_email_subject(text) or _extract_topic(text)
        entities["body_hint"] = _extract_email_body_hint(text)

    elif intent == "revise_draft":
        entities["revision_instruction"] = text
        entities["tone"] = _extract_tone(text)

    elif intent == "revise_reply_draft":
        entities["revision_instruction"] = text
        entities["tone"] = _extract_tone(text)

    elif intent == "follow_up_reminder":
        entities["timeframe"] = _extract_timeframe(text) or "next week"
        entities["topic"] = _extract_topic(text)
        entities["channel"] = "email"

    return {
        "intent": intent,
        "entities": entities,
        "mode": "fallback_rules",
        "note": "Rule-based NLP used (LLM optional).",
        "original_text": text,
    }


# -----------------------
# LLM PARSER
# -----------------------

def _safe_json_load(s: str) -> Optional[dict]:
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def _normalize_llm_result(text: str, obj: dict) -> Dict[str, Any]:
    intent = obj.get("intent") if isinstance(obj, dict) else None
    entities = obj.get("entities") if isinstance(obj, dict) else None
    allowed = {
        "meeting_scheduling", "email_drafting", "draft_email_and_create_event",
        "follow_up_reminder", "list_events", "create_event", "list_emails",
        "read_email", "reply_email", "reply_and_create_event",
        "revise_draft", "revise_reply_draft",
        "unknown",
    }
    if intent not in allowed:
        intent = "unknown"
    if not isinstance(entities, dict):
        entities = {}

    if intent == "meeting_scheduling":
        entities.setdefault("participants", _extract_participants(text))
        entities.setdefault("timeframe", _extract_timeframe(text))
        entities.setdefault("meeting_type", _extract_meeting_type(text))
        entities.setdefault("duration_min", _extract_duration_min(text) or 30)
        entities.setdefault("attendee_emails", _extract_attendee_emails(text))
        entities.setdefault("attendee_names", _extract_attendee_names(text))
        entities.setdefault("title", _extract_event_title(text))

    if intent == "email_drafting":
        entities.setdefault("recipient", _extract_recipient(text))
        entities.setdefault("topic", _extract_topic(text))
        entities.setdefault("tone", _extract_tone(text))
        entities.setdefault("subject", _extract_email_subject(text) or _extract_topic(text))
        entities.setdefault("body_hint", _extract_email_body_hint(text))

    if intent == "draft_email_and_create_event":
        entities.setdefault("recipient", _extract_recipient(text))
        entities.setdefault("topic", _extract_topic(text))
        entities.setdefault("tone", _extract_tone(text))
        entities.setdefault("subject", _extract_email_subject(text) or _extract_topic(text) or "Meeting")
        entities.setdefault("body_hint", _extract_email_body_hint(text))
        entities.setdefault("title", _extract_event_title(text) or "Meeting")
        entities.setdefault("timeframe", _extract_timeframe(text))
        entities.setdefault("start_hint", _extract_start_hint(text))
        entities.setdefault("duration_min", _extract_duration_min(text) or 30)
        entities.setdefault("attendee_emails", _extract_attendee_emails(text))
        entities.setdefault("attendee_names", _extract_attendee_names(text))
        recipient = entities.get("recipient")
        attendee_emails = entities.get("attendee_emails") or []
        if recipient and recipient not in attendee_emails:
            entities["attendee_emails"] = _dedupe_keep_order(attendee_emails + [recipient])

    if intent == "reply_email":
        entities.setdefault("email_reference", _extract_email_reference(text) or "latest")
        entities.setdefault("email_index", _extract_email_index(text))
        entities.setdefault("body_hint", _extract_email_body_hint(text))
        entities.setdefault("tone", _extract_tone(text))

    if intent == "reply_and_create_event":
        entities.setdefault("email_reference", _extract_email_reference(text) or "latest")
        entities.setdefault("email_index", _extract_email_index(text))
        entities.setdefault("body_hint", _extract_email_body_hint(text))
        entities.setdefault("tone", _extract_tone(text))
        entities.setdefault("title", _extract_event_title(text) or "Meeting")
        entities.setdefault("timeframe", _extract_timeframe(text))
        entities.setdefault("start_hint", _extract_start_hint(text))
        entities.setdefault("duration_min", _extract_duration_min(text) or 30)
        entities.setdefault("attendee_emails", _extract_attendee_emails(text))
        entities.setdefault("attendee_names", _extract_attendee_names(text))

    if intent == "revise_draft":
        entities.setdefault("revision_instruction", text)
        entities.setdefault("tone", _extract_tone(text))

    if intent == "revise_reply_draft":
        entities.setdefault("revision_instruction", text)
        entities.setdefault("tone", _extract_tone(text))

    if intent == "follow_up_reminder":
        entities.setdefault("timeframe", _extract_timeframe(text) or "next week")
        entities.setdefault("topic", _extract_topic(text))
        entities.setdefault("channel", "email")

    if intent == "list_events":
        entities.setdefault("days", _extract_days(text))

    if intent == "create_event":
        entities.setdefault("raw", text)
        entities.setdefault("title", _extract_event_title(text))
        entities.setdefault("timeframe", _extract_timeframe(text))
        entities.setdefault("start_hint", _extract_start_hint(text))
        entities.setdefault("duration_min", _extract_duration_min(text) or 30)
        entities.setdefault("attendee_emails", _extract_attendee_emails(text))
        entities.setdefault("attendee_names", _extract_attendee_names(text))

    if intent == "list_emails":
        entities.setdefault("max_results", 5)

    if intent == "read_email":
        entities.setdefault("email_reference", _extract_email_reference(text) or "latest")
        entities.setdefault("email_index", _extract_email_index(text))

    return {
        "intent": intent,
        "entities": entities,
        "mode": "llm_openai",
        "note": "LLM used for intent parsing.",
        "original_text": text,
    }


def _parse_intent_llm(text: str, last_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not _client:
        raise RuntimeError("LLM not available")

    system = """You are an intent parser for an AI executive assistant.
Return ONLY valid JSON with exactly two keys: "intent" and "entities". No extra text, no markdown, no backticks.

ALLOWED INTENTS:
- create_event: user wants to create/schedule/add a calendar event or meeting
- meeting_scheduling: user wants to FIND a free time or check availability (no specific time given)
- list_events: user wants to see/show/list their calendar or upcoming events
- list_emails: user wants to see/show/list their emails or inbox
- read_email: user wants to read/open a specific email
- reply_email: user wants to reply/respond to an email (including "respond to the most recent email")
- email_drafting: user wants to draft/write/compose a NEW email (not reply)
- reply_and_create_event: user wants to reply to an email AND create a calendar event
- draft_email_and_create_event: user wants to draft an email AND create a calendar event
- revise_draft: user is editing a previously generated draft email
- revise_reply_draft: user is editing a previously generated reply draft
- follow_up_reminder: user wants a follow-up or reminder
- unknown: anything else

CRITICAL:
- If the user says "reply", "respond", or mentions replying to an email, the intent is ALWAYS "reply_email", NEVER "email_drafting".
- If there is previous context showing that a draft email or reply draft was just created, and the user's new message does NOT clearly request a new task, then classify it as:
  - "revise_draft" for draft email edits
  - "revise_reply_draft" for reply draft edits
- Treat short follow-up messages after a generated draft as revision instructions by default, even if they are vague.
- Examples of revision follow-ups:
  "shorter"
  "make it warmer"
  "less formal"
  "too long"
  "say I attached it"
  "mention Friday"
  "one line"
  "clean this up"
  "not like that"
  "make it sound better"
- Only choose a new action intent if the user is clearly starting a different task.

ENTITY EXTRACTION RULES:
- title: the event/meeting name
- timeframe: ALWAYS use ISO format "YYYY-MM-DD" for specific dates
- start_hint: combine date and time if both present
- duration_min: integer minutes (default 30)
- attendee_emails: array of email addresses found
- recipient: email address or name for email drafts
- body_hint: the message content the user wants to say
- revision_instruction: the user's requested change to the existing draft
- tone: "professional", "friendly", or "neutral"
- email_reference: "latest", "first", or "indexed"
- max_results: integer for list operations

If unsure use intent="unknown" and entities={}.
"""

    context_blob = json.dumps(last_context or {}, ensure_ascii=False)

    resp = _client.chat.completions.create(
        model=_DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Previous context: {context_blob}\n\nText: {text}"},
        ],
        temperature=0,
    )

    content = (resp.choices[0].message.content or "").strip()
    obj = _safe_json_load(content)
    if not obj:
        raise ValueError("LLM did not return valid JSON")

    return _normalize_llm_result(text, obj)


# -----------------------
# PUBLIC API
# -----------------------

def parse_intent(text: str, last_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {
            "intent": "unknown",
            "entities": {},
            "mode": "fallback_rules",
            "note": "Empty text. Rule-based NLP used (LLM optional).",
            "original_text": "",
        }

    if USE_LLM and _client:
        try:
            return _parse_intent_llm(text, last_context)
        except Exception as e:
            out = _parse_intent_rules(text, last_context)
            out["note"] = (
                "AI unavailable (quota/billing/auth/etc). Returned rule-based intent parsing. "
                f"({type(e).__name__})"
            )
            return out

    return _parse_intent_rules(text, last_context)