import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="ExecAI Backend")


class ParseIntentRequest(BaseModel):
    text: str


def _extract_participants(text: str):
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


def _extract_timeframe(text: str):
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
    if any(w in t for w in ["call", "phone call", "zoom", "teams"]):
        return "call"
    if any(w in t for w in ["meeting", "meet", "sync", "catch up"]):
        return "meeting"
    return "unknown"


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/parse-intent")
def parse_intent(payload: ParseIntentRequest):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required.")

    participants = _extract_participants(text)
    timeframe = _extract_timeframe(text)
    meeting_type = _extract_meeting_type(text)

    return {
        "intent": "meeting_scheduling",
        "participants": participants,
        "timeframe": timeframe,
        "meeting_type": meeting_type,
        "original_text": text,
    }

