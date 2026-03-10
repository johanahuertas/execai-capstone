# backend/intent.py
import os
import re
import json
from typing import Any, Dict, Optional

# -----------------------
# OPTIONAL OpenAI (LLM)
# -----------------------
USE_LLM = bool(os.getenv("OPENAI_API_KEY"))

_client = None
if USE_LLM:
    try:
        from openai import OpenAI

        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    except Exception:
        _client = None
        USE_LLM = False


# -----------------------
# RULE-BASED HELPERS
# -----------------------
def _extract_participants(text: str) -> Optional[int]:
    t = (text or "").lower()

    if "all four of us" in t or "the four of us" in t:
        return 4
    if "all three of us" in t or "the three of us" in t:
        return 3
    if "both of us" in t or "the two of us" in t:
        return 2

    m = re.search(r"\b(\d+)\b", t)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 50:
                return n
        except ValueError:
            pass

    word_map = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    for w, n in word_map.items():
        if re.search(rf"\b{w}\b", t):
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

    return None


def _extract_meeting_type(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in ["zoom", "teams", "call", "phone call"]):
        return "call"
    if any(w in t for w in ["meeting", "meet", "sync", "catch up"]):
        return "meeting"
    return "unknown"


def _extract_duration_min(text: str) -> Optional[int]:
    t = (text or "").lower()

    m = re.search(r"\b(\d{1,3})\s*(min|mins|minute|minutes)\b", t)
    if m:
        n = int(m.group(1))
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

    # 1) full email address first
    email_match = re.search(
        r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b",
        t,
    )
    if email_match:
        return email_match.group(0).lower()

    # 2) email Sarah
    m = re.search(r"\bemail\s+([A-Z][a-z]+)\b", t)
    if m:
        return m.group(1).lower()

    # 3) draft an email to Sarah
    m2 = re.search(r"\bto\s+([A-Z][a-z]+)\b", t)
    if m2 and "email" in t.lower():
        return m2.group(1).lower()

    # 4) lowercase support: "draft an email to sarah"
    m3 = re.search(r"\bto\s+([a-z]+)\b", t.lower())
    if m3 and "email" in t.lower():
        return m3.group(1).lower()

    return None


def _extract_topic(text: str) -> Optional[str]:
    t = (text or "").lower()

    for topic in [
        "invoice",
        "contract",
        "proposal",
        "meeting",
        "payment",
        "follow-up",
        "reminder",
        "schedule",
        "availability",
        "budget",
    ]:
        if topic in t:
            return topic

    return None


def _extract_email_subject(text: str) -> Optional[str]:
    t = (text or "").strip()

    # subject "X"
    m = re.search(r'subject\s+"([^"]+)"', t, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # about X
    m2 = re.search(r"\babout\s+(.+)", t, re.IGNORECASE)
    if m2:
        candidate = m2.group(1).strip().strip('"').strip("'")
        candidate = re.split(r"\bin a\b|\bwith a\b|\busing a\b", candidate, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        if candidate:
            return candidate[:120]

    return None


def _extract_email_body_hint(text: str) -> Optional[str]:
    t = (text or "").strip()

    # saying "..."
    m = re.search(r'saying\s+"([^"]+)"', t, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # say "..."
    m2 = re.search(r'say\s+"([^"]+)"', t, re.IGNORECASE)
    if m2:
        return m2.group(1).strip()

    return None


def _extract_email_reference(text: str) -> Optional[str]:
    t = (text or "").lower()

    if "latest email" in t or "most recent email" in t or "last email" in t:
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


# -----------------------
# CALENDAR HELPERS
# -----------------------
def _has_word(t: str, word: str) -> bool:
    """Word-boundary check to avoid substring bugs."""
    return re.search(rf"\b{re.escape(word)}\b", t) is not None


def _looks_like_list_events(text: str) -> bool:
    t = (text or "").lower()

    calendar_words = ["calendar", "events", "event", "agenda", "schedule"]
    list_words = ["list", "show", "see", "get", "view", "whats", "what's"]

    has_calendar = any(_has_word(t, w) for w in calendar_words)
    has_list = any(_has_word(t, v) for v in list_words)

    return has_calendar and has_list


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

    return 7


def _looks_like_create_event(text: str) -> bool:
    t = (text or "").lower()

    create_phrases = [
        "create event",
        "create an event",
        "create a calendar event",
        "add event",
        "add an event",
        "schedule event",
        "schedule an event",
        "book event",
        "book an event",
        "add to my calendar",
        "put on my calendar",
    ]
    if any(p in t for p in create_phrases):
        return True

    has_action = any(_has_word(t, w) for w in ["create", "add", "schedule", "book", "make"])
    has_target = any(_has_word(t, w) for w in ["event", "meeting", "appointment"])
    return has_action and has_target


# -----------------------
# EMAIL HELPERS
# -----------------------
def _looks_like_list_emails(text: str) -> bool:
    t = (text or "").lower()

    phrases = [
        "list my emails",
        "show my emails",
        "show my latest emails",
        "show my recent emails",
        "list my recent emails",
        "check my inbox",
        "show my inbox",
        "read my emails",
        "list my latest emails",
        "show latest emails",
        "show recent emails",
        "read my inbox",
    ]
    return any(phrase in t for phrase in phrases)


def _looks_like_read_email(text: str) -> bool:
    t = (text or "").lower()

    phrases = [
        "read my latest email",
        "open my latest email",
        "read the latest email",
        "open the latest email",
        "read my most recent email",
        "open my most recent email",
        "read the first email",
        "open the first email",
    ]
    if any(phrase in t for phrase in phrases):
        return True

    # support "open email 1", "read email 2"
    if re.search(r"\b(open|read)\s+email\s+\d+\b", t):
        return True

    return False


def _looks_like_email_drafting(text: str) -> bool:
    t = (text or "").lower()

    drafting_phrases = [
        "draft an email",
        "draft email",
        "write an email",
        "write email",
        "compose an email",
        "compose email",
        "create a draft",
        "create draft",
    ]
    return any(p in t for p in drafting_phrases)


# -----------------------
# RULE-BASED CLASSIFIER
# -----------------------
def _classify_intent_rules(text: str) -> str:
    t = (text or "").lower()

    # IMPORTANT: order matters
    if _looks_like_create_event(t):
        return "create_event"

    if _looks_like_list_events(t):
        return "list_events"

    if _looks_like_read_email(t):
        return "read_email"

    if _looks_like_list_emails(t):
        return "list_emails"

    if _looks_like_email_drafting(t):
        return "email_drafting"

    if any(w in t for w in ["follow up", "follow-up", "remind", "reminder"]):
        return "follow_up_reminder"

    if any(w in t for w in ["send an email", "email", "write an email"]):
        return "email_drafting"

    if any(w in t for w in ["schedule", "find a time", "set up a meeting", "book a meeting", "meet"]):
        return "meeting_scheduling"

    return "unknown"


def _parse_intent_rules(text: str) -> Dict[str, Any]:
    intent = _classify_intent_rules(text)
    entities: Dict[str, Any] = {}

    if intent == "list_events":
        entities["days"] = _extract_days(text)

    elif intent == "create_event":
        entities["raw"] = text
        entities["timeframe"] = _extract_timeframe(text)
        entities["duration_min"] = _extract_duration_min(text) or 30

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

    elif intent == "email_drafting":
        entities["recipient"] = _extract_recipient(text)
        entities["topic"] = _extract_topic(text)
        entities["tone"] = _extract_tone(text)
        entities["subject"] = _extract_email_subject(text) or _extract_topic(text)
        entities["body_hint"] = _extract_email_body_hint(text)

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
# LLM PARSER (OPTIONAL)
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
        "meeting_scheduling",
        "email_drafting",
        "follow_up_reminder",
        "list_events",
        "create_event",
        "list_emails",
        "read_email",
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

    if intent == "email_drafting":
        entities.setdefault("recipient", _extract_recipient(text))
        entities.setdefault("topic", _extract_topic(text))
        entities.setdefault("tone", _extract_tone(text))
        entities.setdefault("subject", _extract_email_subject(text) or _extract_topic(text))
        entities.setdefault("body_hint", _extract_email_body_hint(text))

    if intent == "follow_up_reminder":
        entities.setdefault("timeframe", _extract_timeframe(text) or "next week")
        entities.setdefault("topic", _extract_topic(text))
        entities.setdefault("channel", "email")

    if intent == "list_events":
        entities.setdefault("days", _extract_days(text))

    if intent == "create_event":
        entities.setdefault("raw", text)
        entities.setdefault("timeframe", _extract_timeframe(text))
        entities.setdefault("duration_min", _extract_duration_min(text) or 30)

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


def _parse_intent_llm(text: str) -> Dict[str, Any]:
    if not _client:
        raise RuntimeError("LLM not available")

    system = (
        "You are an intent parser for an executive assistant. "
        "Return ONLY valid JSON with keys: intent, entities. "
        "Allowed intents: meeting_scheduling, email_drafting, follow_up_reminder, list_events, create_event, list_emails, read_email, unknown. "
        "entities should be an object. "
        "If unsure, use intent='unknown' and entities={}. "
        "Do NOT include extra text."
    )

    resp = _client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Text: {text}"},
        ],
        temperature=0,
    )

    content = (resp.choices[0].message.content or "").strip()
    obj = _safe_json_load(content)
    if not obj:
        raise ValueError("LLM did not return valid JSON")

    return _normalize_llm_result(text, obj)


# -----------------------
# PUBLIC FUNCTION
# -----------------------
def parse_intent(text: str) -> Dict[str, Any]:
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
            return _parse_intent_llm(text)
        except Exception as e:
            out = _parse_intent_rules(text)
            out["note"] = (
                "AI unavailable (quota/billing/auth/etc). Returned rule-based intent parsing. "
                f"({type(e).__name__})"
            )
            return out

    return _parse_intent_rules(text)