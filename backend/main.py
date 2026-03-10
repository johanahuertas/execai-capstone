# backend/main.py
from datetime import datetime, timedelta
from typing import Optional, Any, Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .intent import parse_intent as parse_intent_ai
from .orchestrator import handle_intent
from .integrations import router as integrations_router

# ✅ Reusable services
from .integrations import (
    list_events_service,
    create_event_service,
    list_emails_service,
    create_gmail_draft_service,
    read_email_service,
)

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
    2) Orchestrator decides
    3) main.py executes integrations when needed
    4) Return structured response
    """
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required.")

    intent_data = parse_intent_ai(text)
    decision = handle_intent(intent_data)

    existing_result = (decision or {}).get("result")
    if existing_result is not None:
        return {
            "intent_data": intent_data,
            "decision": decision,
            "result": existing_result,
        }

    entities: Dict[str, Any] = (intent_data or {}).get("entities") or {}

    action = ((decision or {}).get("action") or (intent_data or {}).get("intent") or "").lower().strip()
    provider = ((decision or {}).get("provider") or "google").lower().strip()

    result = None

    try:
        # -----------------------
        # LIST EVENTS
        # -----------------------
        if action in {"list_events", "calendar_list", "get_events"}:
            days = (decision or {}).get("days")
            if days is None:
                days = entities.get("days", 7)

            result = list_events_service(
                provider=provider,
                days=int(days),
            )

        # -----------------------
        # CREATE EVENT
        # -----------------------
        elif action in {"create_event", "calendar_create", "schedule_event"}:
            title = (decision or {}).get("title") or entities.get("title")
            start = (decision or {}).get("start") or entities.get("start")
            duration_min = (decision or {}).get("duration_min") or entities.get("duration_min") or 30
            attendee_emails = (decision or {}).get("attendee_emails") or entities.get("attendee_emails") or []

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
                    attendees=attendee_emails,
                )

        # -----------------------
        # LIST EMAILS
        # -----------------------
        elif action in {"list_emails", "get_emails", "show_inbox"}:
            max_results = (decision or {}).get("max_results")
            if max_results is None:
                max_results = entities.get("max_results", 5)

            result = list_emails_service(
                provider=provider,
                max_results=int(max_results),
            )

        # -----------------------
        # READ EMAIL
        # -----------------------
        elif action in {"read_email", "open_email"}:
            email_reference = (decision or {}).get("email_reference") or entities.get("email_reference") or "latest"
            email_index = (decision or {}).get("email_index")
            if email_index is None:
                email_index = entities.get("email_index")

            # latest email
            if email_reference == "latest":
                latest_list = list_emails_service(provider=provider, max_results=1)
                emails = latest_list.get("emails", []) or []

                if not emails:
                    result = {
                        "status": "not_found",
                        "message": "No emails found in your inbox.",
                    }
                else:
                    message_id = emails[0].get("id")
                    result = read_email_service(provider=provider, message_id=message_id)

            # indexed email, e.g. "email 1"
            elif email_reference in {"indexed", "first"}:
                index = 1
                if email_reference == "first":
                    index = 1
                elif email_index:
                    try:
                        index = max(1, int(email_index))
                    except Exception:
                        index = 1

                email_list = list_emails_service(provider=provider, max_results=max(index, 1))
                emails = email_list.get("emails", []) or []

                if len(emails) < index:
                    result = {
                        "status": "not_found",
                        "message": f"I couldn't find email #{index}.",
                    }
                else:
                    message_id = emails[index - 1].get("id")
                    result = read_email_service(provider=provider, message_id=message_id)

            else:
                result = {
                    "status": "needs_clarification",
                    "message": "I can read an email, but I need a clearer reference like 'latest email' or 'email 1'.",
                }

        # -----------------------
        # CREATE GMAIL DRAFT
        # -----------------------
        elif action in {"create_draft", "draft_email"}:
            recipient = (decision or {}).get("recipient") or entities.get("recipient")
            subject = (decision or {}).get("subject") or entities.get("subject") or "Quick Follow-Up"
            body = (decision or {}).get("body") or entities.get("body_hint") or ""

            if not recipient:
                result = {
                    "status": "needs_clarification",
                    "missing": ["recipient"],
                    "message": "I can create the Gmail draft, but I need the recipient email address.",
                    "example": 'Try: "Draft an email to sarah@example.com about the proposal"',
                }
            elif "@" not in str(recipient):
                result = {
                    "status": "needs_clarification",
                    "missing": ["recipient_email"],
                    "message": f'I understood the recipient as "{recipient}", but I need the full email address to create a real Gmail draft.',
                    "example": f'Draft an email to {recipient}@example.com about the proposal',
                }
            else:
                result = create_gmail_draft_service(
                    provider=provider,
                    to=str(recipient),
                    subject=str(subject),
                    body=str(body),
                )

    except HTTPException as e:
        result = {
            "status": "error",
            "where": "integrations",
            "detail": e.detail,
        }
    except Exception as e:
        result = {
            "status": "error",
            "where": "assistant",
            "detail": f"{type(e).__name__}: {str(e)}",
        }

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
    return {
        "intent": "meeting_scheduling",
        "options": options,
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
        "email": {
            "to": recipient,
            "subject": subject,
            "body": body,
            "tone": tone,
            "provider": "mock",
        },
        "message": "Email draft generated (mock).",
    }