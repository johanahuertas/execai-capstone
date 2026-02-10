import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from openai import RateLimitError

# Optional AI parser (may fail due to quota). We keep it to match capstone spec.
from .intent import parse_intent as parse_intent_ai

app = FastAPI(title="ExecAI Backend")


# -----------------------
# MODELS
# -----------------------
class ParseIntentRequest(BaseModel):
    text: str


class CreateEventRequest(BaseModel):
    title: str
    start: str
    duration_min: int


# -----------------------
# FALLBACK NLP (Rules / Heuristics)
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
    t = text.lower()
    if any(w in t for w in ["call", "phone call", "zoom", "teams", "meet"]):
        return "call"
    if any(w in t for w in ["meeting", "meet", "sync", "catch up"]):
        return "meeting"
    return "unknown"


def _extract_email_recipient(text: str) -> Optional[str]:
    """
    Very lightweight heuristic:
    - "email Sarah ..." -> recipient = sarah
    - "send an email to sarah ..." -> recipient = sarah
    - If an actual email appears, return it.
    """
    t = text.strip()

    m_email = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", t)
    if m_email:
        return m_email.group(0).lower()

    m = re.search(r"\b(email|mail|send an email to|send email to)\s+([A-Za-z]+)\b", t, re.IGNORECASE)
    if m:
        return m.group(2).lower()

    return None


def _extract_topic(text: str) -> Optional[str]:
    """
    Very rough topic extraction: look for keywords that commonly appear in requests.
    """
    t = text.lower()
    for kw in ["invoice", "contract", "proposal", "resume", "payment", "meeting", "follow up", "follow-up"]:
        if kw in t:
            return kw.replace(" ", "_")
    return None


def _extract_tone(text: str) -> str:
    t = text.lower()
    if "formal" in t or "professionally" in t or "professional" in t:
        return "professional"
    if "short" in t or "brief" in t:
        return "short"
    if "friendly" in t:
        return "friendly"
    return "professional"


def _classify_intent_rules(text: str) -> str:
    """
    3 intents:
    - meeting_scheduling
    - email_drafting
    - follow_up_reminder
    """
    t = text.lower()

    # Follow-up / reminder intent first (so "schedule a follow-up" doesn't get treated as meeting scheduling)
    if any(p in t for p in ["follow up", "follow-up", "remind me", "reminder", "check in"]):
        return "follow_up_reminder"

    # Email drafting
    if any(p in t for p in ["email", "mail", "send an email", "draft an email", "write an email"]):
        return "email_drafting"

    # Meeting scheduling
    if any(p in t for p in ["schedule", "set up a meeting", "find a time", "meet", "meeting", "calendar"]):
        return "meeting_scheduling"

    return "unknown"


def _parse_with_rules(text: str) -> Dict[str, Any]:
    intent = _classify_intent_rules(text)

    entities: Dict[str, Any] = {}

    if intent == "meeting_scheduling":
        entities = {
            "participants": _extract_participants(text),
            "timeframe": _extract_timeframe(text),
            "meeting_type": _extract_meeting_type(text),
            "duration_min": 30,
        }

    elif intent == "email_drafting":
        entities = {
            "recipient": _extract_email_recipient(text),
            "topic": _extract_topic(text),
            "tone": _extract_tone(text),
        }

    elif intent == "follow_up_reminder":
        entities = {
            "timeframe": _extract_timeframe(text) or "next week",
            "topic": _extract_topic(text),
            "channel": "email",  # mock suggestion
        }

    return {
        "intent": intent,
        "entities": entities,
        "mode": "fallback_rules",
        "note": "Rule-based NLP used (LLM optional).",
        "original_text": text,
    }


# -----------------------
# ENDPOINTS
# -----------------------
@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/parse-intent")
def parse_intent_endpoint(payload: ParseIntentRequest):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required.")

    # Try AI parser first (if available). If quota/billing blocks it, fallback to rules.
    try:
        ai_result = parse_intent_ai(text)
        if isinstance(ai_result, dict):
            # normalize to our contract if AI result is different
            # If your intent.py already returns {intent, entities}, great.
            if "entities" not in ai_result:
                ai_result = {
                    "intent": ai_result.get("intent", "unknown"),
                    "entities": {k: v for k, v in ai_result.items() if k not in ["intent", "original_text", "mode", "note"]},
                    "original_text": ai_result.get("original_text", text),
                }
            ai_result["mode"] = "ai"
            return ai_result

        # If AI returned something unexpected, fallback
        return _parse_with_rules(text)

    except RateLimitError:
        return _parse_with_rules(text)
    except Exception:
        # Any other unexpected error: still return fallback so demo never breaks
        return _parse_with_rules(text)


@app.post("/suggest-times")
def suggest_times(payload: ParseIntentRequest):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required.")

    now = datetime.now()
    options = [
        {"label": "Option A", "start": (now + timedelta(days=1, hours=10)).isoformat(), "duration_min": 30},
        {"label": "Option B", "start": (now + timedelta(days=2, hours=14)).isoformat(), "duration_min": 30},
        {"label": "Option C", "start": (now + timedelta(days=3, hours=9)).isoformat(), "duration_min": 30},
    ]

    return {
        "intent": "meeting_scheduling",
        "options": options,
        "original_text": text,
        "provider": "mock",
    }


@app.post("/create-event")
def create_event(req: CreateEventRequest):
    return {
        "status": "created",
        "event": {
            "title": req.title,
            "start": req.start,
            "duration_min": req.duration_min,
            "provider": "mock",
        },
        "message": "Event created successfully (mock).",
        "provider": "mock",
    }
