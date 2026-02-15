# backend/main.py
from datetime import datetime, timedelta
from typing import Optional, Any, Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .intent import parse_intent as parse_intent_ai
from .orchestrator import handle_intent
from .integrations import router as integrations_router

# ✅ Reusable services (Google real + mock)
from .integrations import list_events_service, create_event_service

app = FastAPI(title="ExecAI Backend")

# Mount integrations endpoints
app.include_router(integrations_router)


# -----------------------
# MODELS
# -----------------------
class ParseIntentRequest(BaseModel):
    text: str


class CreateEventRequest(BaseModel):
    title: str
    start: str
    duration_min: int = 30


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
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required.")
    return parse_intent_ai(text)


@app.post("/assistant")
def assistant(payload: ParseIntentRequest):
    """
    Main agent entry point.

    1) Understand intent (hybrid NLP)
    2) Orchestrator decides (and may execute)
    3) If orchestrator didn't execute, main executes via integrations services
    4) Return structured response
    """
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required.")

    intent_data = parse_intent_ai(text)
    decision = handle_intent(intent_data)

    # If orchestrator already produced a result, return it as-is
    existing_result = (decision or {}).get("result")
    if existing_result is not None:
        return {
            "intent_data": intent_data,
            "decision": decision,
            "result": existing_result,
        }

    # -----------------------
    # Fallback execution in main.py (if orchestrator only "decided")
    # -----------------------
    entities: Dict[str, Any] = (intent_data or {}).get("entities") or {}

    action = ((decision or {}).get("action") or (intent_data or {}).get("intent") or "").lower().strip()
    provider = ((decision or {}).get("provider") or "google").lower().strip()

    result = None

    try:
        # LIST EVENTS
        if action in {"list_events", "calendar_list", "get_events"}:
            days = (decision or {}).get("days")
            if days is None:
                days = entities.get("days", 7)
            result = list_events_service(provider=provider, days=int(days))

        # CREATE EVENT
        elif action in {"create_event", "calendar_create", "schedule_event"}:
            title = (decision or {}).get("title") or entities.get("title")
            start = (decision or {}).get("start") or entities.get("start")
            duration_min = (decision or {}).get("duration_min") or entities.get("duration_min") or 30

            if not title or not start:
                result = {
                    "status": "needs_clarification",
                    "missing": [k for k in ["title", "start"] if not (title if k == "title" else start)],
                    "message": "I can create the event, but I need at least a title and a start time.",
                    "example": 'Try: "Create an event called Strategy Sync tomorrow at 2pm for 30 minutes"',
                }
            else:
                result = create_event_service(
                    provider=provider,
                    title=str(title),
                    start=str(start),
                    duration_min=int(duration_min),
                )

    except HTTPException as e:
        result = {"status": "error", "where": "integrations", "detail": e.detail}
    except Exception as e:
        result = {"status": "error", "where": "assistant", "detail": f"{type(e).__name__}: {str(e)}"}

    return {
        "intent_data": intent_data,
        "decision": decision,
        "result": result,
    }


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
    return {"intent": "meeting_scheduling", "options": options, "provider": "mock"}


@app.post("/create-event")
def create_event(req: CreateEventRequest):
    # Mock event creation endpoint (separado de Google real)
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
        "email": {"to": recipient, "subject": subject, "body": body, "tone": tone, "provider": "mock"},
        "message": "Email draft generated (mock).",
    }
