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
        from openai import OpenAIError
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    except Exception:
        # If openai isn't installed or fails to init, fallback silently
        _client = None
        USE_LLM = False


# -----------------------
# RULE-BASED HELPERS
# -----------------------
def _extract_participants(text: str) -> Optional[int]:
    t = text.lower()

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
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10
    }
    for w, n in word_map.items():
        if re.search(rf"\b{w}\b", t):
            return n

    return None


def _extract_timeframe(text: str) -> Optional[str]:
    t = text.lower()

    # common phrases
    if "next week" in t:
        return "next week"
    if "this week" in t:
        return "this week"
    if "tomorrow" in t:
        return "tomorrow"
    if "today" in t:
        return "today"

    # weekdays
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
    t = text.lower()
    if any(w in t for w in ["zoom", "teams", "call", "phone call"]):
        return "call"
    if any(w in t for w in ["meeting", "meet", "sync", "catch up"]):
        return "meeting"
    return "unknown"


def _extract_duration_min(text: str) -> Optional[int]:
    t = text.lower()

    # "30 min", "45 minutes"
    m = re.search(r"\b(\d{1,3})\s*(min|mins|minute|minutes)\b", t)
    if m:
        n = int(m.group(1))
        if 5 <= n <= 240:
            return n

    # common defaults like "half an hour"
    if "half an hour" in t:
        return 30
    if "an hour" in t or "1 hour" in t:
        return 60

    return None


def _extract_tone(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["professional", "formally", "formal"]):
        return "professional"
    if any(w in t for w in ["friendly", "casual"]):
        return "friendly"
    return "neutral"


def _extract_recipient(text: str) -> Optional[str]:
    # super simple: "email Sarah ..." => sarah
    t = text.strip()
    m = re.search(r"\bemail\s+([A-Z][a-z]+)\b", t)
    if m:
        return m.group(1).lower()
    # "to Sarah" heuristic
    m2 = re.search(r"\bto\s+([A-Z][a-z]+)\b", t)
    if m2 and "email" in t.lower():
        return m2.group(1).lower()
    return None


def _extract_topic(text: str) -> Optional[str]:
    t = text.lower()
    # common business topics
    for topic in ["invoice", "contract", "proposal", "meeting", "payment", "follow-up", "reminder"]:
        if topic in t:
            return topic
    return None


def _classify_intent_rules(text: str) -> str:
    t = text.lower()

    # follow-up / reminder
    if any(w in t for w in ["follow up", "follow-up", "remind", "reminder"]):
        return "follow_up_reminder"

    # email drafting
    if any(w in t for w in ["email", "draft an email", "write an email", "send an email"]):
        return "email_drafting"

    # meeting scheduling
    if any(w in t for w in ["schedule", "find a time", "set up a meeting", "book a meeting", "meet"]):
        return "meeting_scheduling"

    return "unknown"


def _parse_intent_rules(text: str) -> Dict[str, Any]:
    intent = _classify_intent_rules(text)

    entities: Dict[str, Any] = {}

    if intent == "meeting_scheduling":
        entities["participants"] = _extract_participants(text)
        entities["timeframe"] = _extract_timeframe(text)
        entities["meeting_type"] = _extract_meeting_type(text)
        entities["duration_min"] = _extract_duration_min(text) or 30

    elif intent == "email_drafting":
        entities["recipient"] = _extract_recipient(text)
        entities["topic"] = _extract_topic(text)
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
# LLM PARSER (OPTIONAL)
# -----------------------
def _safe_json_load(s: str) -> Optional[dict]:
    try:
        return json.loads(s)
    except Exception:
        # try to extract json block if model adds text
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def _normalize_llm_result(text: str, obj: dict) -> Dict[str, Any]:
    """
    Forces a stable response schema even if LLM output is missing fields.
    """
    intent = obj.get("intent") if isinstance(obj, dict) else None
    entities = obj.get("entities") if isinstance(obj, dict) else None

    if intent not in {"meeting_scheduling", "email_drafting", "follow_up_reminder", "unknown"}:
        intent = "unknown"

    if not isinstance(entities, dict):
        entities = {}

    # Light normalization defaults
    if intent == "meeting_scheduling":
        entities.setdefault("participants", _extract_participants(text))
        entities.setdefault("timeframe", _extract_timeframe(text))
        entities.setdefault("meeting_type", _extract_meeting_type(text))
        entities.setdefault("duration_min", _extract_duration_min(text) or 30)

    if intent == "email_drafting":
        entities.setdefault("recipient", _extract_recipient(text))
        entities.setdefault("topic", _extract_topic(text))
        entities.setdefault("tone", _extract_tone(text))

    if intent == "follow_up_reminder":
        entities.setdefault("timeframe", _extract_timeframe(text) or "next week")
        entities.setdefault("topic", _extract_topic(text))
        entities.setdefault("channel", "email")

    return {
        "intent": intent,
        "entities": entities,
        "mode": "llm_openai",
        "note": "LLM used for intent parsing.",
        "original_text": text,
    }


def _parse_intent_llm(text: str) -> Dict[str, Any]:
    """
    Uses OpenAI if available; otherwise caller should fallback.
    Never throws: raises Exception only to let caller fallback.
    """
    if not _client:
        raise RuntimeError("LLM not available")

    system = (
        "You are an intent parser for an executive assistant. "
        "Return ONLY valid JSON with keys: intent, entities. "
        "Allowed intents: meeting_scheduling, email_drafting, follow_up_reminder, unknown. "
        "entities should be an object. "
        "If unsure, use intent='unknown' and entities={}. "
        "Do NOT include extra text."
    )

    user = f"Text: {text}"

    # Use a lightweight model name; you can change later
    resp = _client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
    )

    content = (resp.choices[0].message.content or "").strip()
    obj = _safe_json_load(content)
    if not obj:
        raise ValueError("LLM did not return valid JSON")

    return _normalize_llm_result(text, obj)


# -----------------------
# PUBLIC FUNCTION (used by main.py)
# -----------------------
def parse_intent(text: str) -> Dict[str, Any]:
    """
    Main entry point. Always returns a stable JSON schema.
    - Tries LLM if configured
    - Falls back to rules on any error (missing key, quota, auth, etc.)
    """
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
            # quota/billing/auth/network/etc -> fallback
            out = _parse_intent_rules(text)
            out["note"] = f"AI unavailable (quota/billing/auth/etc). Returned rule-based intent parsing. ({type(e).__name__})"
            return out

    return _parse_intent_rules(text)
