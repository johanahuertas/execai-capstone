# backend/main.py
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .intent import parse_intent as parse_intent_ai
from .orchestrator import handle_intent
from .integrations import router as integrations_router  # ✅ NEW

app = FastAPI(title="ExecAI Backend")

# ✅ NEW: mount integrations endpoints (Google/Microsoft mock)
app.include_router(integrations_router)


# -----------------------
# MODELS
# -----------------------
class ParseIntentRequest(BaseModel):
    text: str


class CreateEventRequest(BaseModel):
    title: str
    start: str
    duration_min: int


class DraftEmailRequest(BaseModel):
    recipient: Optional[str] = None
    topic: Optional[str] = None
    tone: Optional[str] = "professional"
    original_text: Optional[str] = None


# -----------------------
# ENDPOINTS
# -----------------------
@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/parse-intent")
def parse_intent_endpoint(payload: ParseIntentRequest):
    """
    Hybrid NLP endpoint.

    Uses:
    - LLM-based intent parsing (OpenAI) IF enabled
    - Rule-based NLP fallback otherwise

    The decision logic lives in intent.py.
    """
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required.")
    return parse_intent_ai(text)


@app.post("/assistant")
def assistant(payload: ParseIntentRequest):
    """
    Main agent entry point.

    1) Understand intent (hybrid NLP)
    2) Decide next action (orchestrator)
    3) Return structured response to UI
    """
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required.")

    intent_data = parse_intent_ai(text)
    decision = handle_intent(intent_data)

    return {
        "intent_data": intent_data,
        "decision": decision,
    }


@app.post("/suggest-times")
def suggest_times(payload: ParseIntentRequest):
    """
    Mock meeting availability.
    (Real calendar integrations are intentionally out of scope for capstone.)
    """
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
        "provider": "mock",
    }


@app.post("/create-event")
def create_event(req: CreateEventRequest):
    """
    Mock event creation.
    (Real calendar writes require OAuth and is out of scope.)
    """
    return {
        "status": "created",
        "event": {
            "title": req.title,
            "start": req.start,
            "duration_min": req.duration_min,
            "provider": "mock",
        },
        "message": "Event created successfully (mock).",
    }


@app.post("/draft-email")
def draft_email(req: DraftEmailRequest):
    """
    Mock email drafting.
    (Real email sending requires OAuth and is out of scope for now.)
    """
    recipient = (req.recipient or "the recipient").strip()
    topic = (req.topic or "your request").strip()
    tone = (req.tone or "professional").strip().lower()

    subject = f"Regarding {topic.title()}" if topic else "Quick Follow-Up"

    if tone == "friendly":
        greeting = f"Hi {recipient},"
        closing = "Thanks so much,\nExecAI (Draft)"
    else:
        greeting = f"Hello {recipient},"
        closing = "Best regards,\nExecAI (Draft)"

    body = (
        f"{greeting}\n\n"
        f"I hope you’re doing well. I’m reaching out regarding {topic}. "
        f"Please let me know the best next step, and if you’d like, I can share any additional details.\n\n"
        f"{closing}"
    )

    return {
        "status": "drafted",
        "email": {
            "to": recipient,
            "subject": subject,
            "body": body,
            "tone": tone,
            "provider": "mock",
        },
        "message": "Email draft generated (mock).",
    }
