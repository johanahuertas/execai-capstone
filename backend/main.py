# backend/main.py
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .intent import parse_intent as parse_intent_ai
from .orchestrator import handle_intent

# ✅ Keep router endpoints
from .integrations import router as integrations_router

# ✅ NEW: import reusable services from integrations
from .integrations import list_events_service, create_event_service

app = FastAPI(title="ExecAI Backend")

# Mount integrations endpoints (Google/Microsoft mock)
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
    2) Execute action (calendar/email/etc.) when possible
    3) Return structured response to UI
    """
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required.")

    intent_data = parse_intent_ai(text)
    decision = handle_intent(intent_data)

    # -----------------------
    # ✅ Connect /assistant -> integrations
    # -----------------------
    # We support two common intent shapes:
    # - intent_data["intent"] == "list_events" / "create_event"
    # - decision["action"] == "list_events" / "create_event"
    action = (decision or {}).get("action") or (intent_data or {}).get("intent") or ""
    action = str(action).lower().strip()

    provider = (intent_data or {}).get("provider") or (decision or {}).get("provider") or "google"
    provider = str(provider).lower().strip()

    # Try to read params from common fields
    days = (intent_data or {}).get("days") or (decision or {}).get("days") or 7

    title = (intent_data or {}).get("title") or (decision or {}).get("title")
    start = (intent_data or {}).get("start") or (decision or {}).get("start")
    duration_min = (intent_data or {}).get("duration_min") or (decision or {}).get("duration_min") or 30

    result = None

    try:
        if action in {"list_events", "calendar_list", "get_events"}:
            result = list_events_service(provider=provider, days=int(days))
        elif action in {"create_event", "calendar_create", "schedule_event"}:
            if not title or not start:
                # If NLP didn't extract enough, return a helpful response instead of failing.
                result = {
                    "status": "needs_clarification",
                    "missing": [k for k in ["title", "start"] if not (title if k == "title" else start)],
                    "message": "I can create the event, but I need at least a title and a start time.",
                    "example": 'Try: "Create event: Team sync tomorrow at 2pm for 30 minutes"',
                }
            else:
                result = create_event_service(
                    provider=provider,
                    title=str(title),
                    start=str(start),
                    duration_min=int(duration_min),
                )
    except HTTPException as e:
        # Surface integration errors cleanly to the UI
        result = {"status": "error", "where": "integrations", "detail": e.detail}

    return {
        "intent_data": intent_data,
        "decision": decision,
        "result": result,
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
