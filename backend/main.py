# backend/main.py

from datetime import datetime, timedelta
from typing import Optional, Any, Dict, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .intent import parse_intent as parse_intent_ai
from .orchestrator import handle_intent
from .integrations import router as integrations_router
from .integrations import (
    list_events_service,
    create_event_service,
    list_emails_service,
    create_gmail_draft_service,
    create_gmail_reply_draft_service,
    read_email_service,
    _extract_email_address,
)
from .ai_drafts import generate_reply_draft

app = FastAPI(title="ExecAI Backend")
app.include_router(integrations_router)


# -----------------------
# MODELS
# -----------------------

class ParseIntentRequest(BaseModel):
    text: str
    provider: Optional[str] = "google"   # ← NEW: frontend sends selected provider


class CreateEventRequest(BaseModel):
    title: str
    start: str
    duration_min: int = 30
    attendee_emails: List[str] = []


class DraftEmailRequest(BaseModel):
    recipient: Optional[str] = None
    topic: Optional[str] = None
    tone: Optional[str] = "professional"
    original_text: Optional[str] = None


# -----------------------
# HELPERS
# -----------------------

def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []

    for item in items or []:
        val = str(item).strip()
        key = val.lower()

        if not val or key in seen:
            continue

        seen.add(key)
        out.append(val)

    return out


def _resolve_target_email(
    provider: str,
    email_reference: str,
    email_index: Optional[int],
) -> Optional[Dict[str, Any]]:

    if email_reference == "latest":
        latest_list = list_emails_service(
            provider=provider,
            max_results=1,
            inbox_only=False,
            primary_only=True,
        )
        emails = latest_list.get("emails", []) or []
        if not emails:
            return None

        message_id = emails[0].get("id")
        if not message_id:
            return None

        read_result = read_email_service(provider=provider, message_id=message_id)
        return (read_result or {}).get("email") or None

    if email_reference in {"indexed", "first"}:
        index = 1

        if email_reference == "first":
            index = 1
        elif email_index:
            try:
                index = max(1, int(email_index))
            except Exception:
                index = 1

        email_list = list_emails_service(
            provider=provider,
            max_results=max(index, 1),
            inbox_only=False,
            primary_only=True,
        )
        emails = email_list.get("emails", []) or []

        if len(emails) < index:
            return None

        message_id = emails[index - 1].get("id")
        if not message_id:
            return None

        read_result = read_email_service(provider=provider, message_id=message_id)
        return (read_result or {}).get("email") or None

    return None


# -----------------------
# BASIC ROUTES
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


# -----------------------
# MAIN ASSISTANT
# -----------------------

@app.post("/assistant")
def assistant(payload: ParseIntentRequest):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required.")

    # Provider comes from the frontend selector — default google
    request_provider = (payload.provider or "google").strip().lower()

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

    # Use request_provider as the source of truth for ALL actions
    # (orchestrator may suggest a provider, but the UI selector wins)
    provider = request_provider

    result = None

    try:
        if action in {"list_events", "calendar_list", "get_events"}:
            days = (decision or {}).get("days")
            if days is None:
                days = entities.get("days", 7)

            result = list_events_service(
                provider=provider,
                days=int(days),
            )

        elif action in {"create_event", "calendar_create", "schedule_event"}:
            title = (decision or {}).get("title") or entities.get("title")
            start = (decision or {}).get("start") or entities.get("start")
            duration_min = (decision or {}).get("duration_min") or entities.get("duration_min") or 30
            attendee_emails = (
                (decision or {}).get("attendee_emails")
                or entities.get("attendee_emails")
                or []
            )
            attendee_emails = _dedupe_keep_order(attendee_emails)

            has_conflicts = bool((decision or {}).get("has_conflicts", False))
            conflicts = (decision or {}).get("conflicts", []) or []
            alternatives = (decision or {}).get("alternatives", []) or []

            if not title or not start:
                result = {
                    "status": "needs_clarification",
                    "missing": [k for k in ["title", "start"] if not (title if k == "title" else start)],
                    "message": "I can create the event, but I need at least a title and a start time.",
                    "example": 'Try: "Create an event called Strategy Sync tomorrow at 2pm for 30 minutes"',
                }
            elif has_conflicts:
                result = {
                    "status": "conflict_detected",
                    "message": "I found a calendar conflict, so I did not create the event.",
                    "conflicts": conflicts,
                    "alternatives": alternatives,
                    "proposed_event": {
                        "title": title,
                        "start": start,
                        "duration_min": int(duration_min),
                        "attendee_emails": attendee_emails,
                    },
                }
            else:
                result = create_event_service(
                    provider=provider,
                    title=str(title),
                    start=str(start),
                    duration_min=int(duration_min),
                    attendees=attendee_emails,
                )

        elif action in {"suggest_times", "meeting_scheduling"}:
            result = {
                "status": "success",
                "options": (decision or {}).get("options", []) or [],
                "attendee_emails": (decision or {}).get("attendee_emails", []) or [],
                "attendee_names": (decision or {}).get("attendee_names", []) or [],
                "duration_min": (decision or {}).get("duration_min") or entities.get("duration_min") or 30,
                "title": (decision or {}).get("title") or entities.get("title"),
                "source": (decision or {}).get("source", "unknown"),
                "message": (decision or {}).get("message", "Suggested meeting times generated."),
                "busy_display": (decision or {}).get("busy_display", []) or [],
            }

        elif action in {"list_emails", "get_emails", "show_inbox"}:
            max_results = (decision or {}).get("max_results")
            if max_results is None:
                max_results = entities.get("max_results", 5)

            result = list_emails_service(
                provider=provider,
                max_results=int(max_results),
                inbox_only=True,
                primary_only=False,
            )

        elif action in {"read_email", "open_email"}:
            email_reference = (decision or {}).get("email_reference") or entities.get("email_reference") or "latest"
            email_index = (decision or {}).get("email_index")
            if email_index is None:
                email_index = entities.get("email_index")

            target_email = _resolve_target_email(
                provider=provider,
                email_reference=email_reference,
                email_index=email_index,
            )

            if not target_email:
                result = {
                    "status": "not_found",
                    "message": "I couldn't find the requested email.",
                }
            else:
                result = {
                    "provider": provider,
                    "email": target_email,
                }

        elif action in {"create_draft", "draft_email"}:
            recipient = (decision or {}).get("recipient") or entities.get("recipient")
            subject = (decision or {}).get("subject") or entities.get("subject") or "Quick Follow-Up"
            body = (decision or {}).get("body") or entities.get("body_hint") or ""

            if not recipient:
                result = {
                    "status": "needs_clarification",
                    "missing": ["recipient"],
                    "message": "I can create the draft, but I need the recipient email address.",
                    "example": 'Try: "Draft an email to sarah@example.com about the proposal"',
                }
            elif "@" not in str(recipient):
                result = {
                    "status": "needs_clarification",
                    "missing": ["recipient_email"],
                    "message": f'I understood the recipient as "{recipient}", but I need the full email address.',
                    "example": f'Draft an email to {recipient}@example.com about the proposal',
                }
            else:
                result = create_gmail_draft_service(
                    provider=provider,
                    to=str(recipient),
                    subject=str(subject),
                    body=str(body),
                )

        elif action in {"reply_email", "create_reply_draft"}:
            email_reference = (decision or {}).get("email_reference") or entities.get("email_reference") or "latest"
            email_index = (decision or {}).get("email_index")
            if email_index is None:
                email_index = entities.get("email_index")

            target_email = _resolve_target_email(
                provider=provider,
                email_reference=email_reference,
                email_index=email_index,
            )

            if not target_email:
                result = {
                    "status": "not_found",
                    "message": "No email found to reply to.",
                }
            else:
                raw_from = target_email.get("from") or ""
                to_email = _extract_email_address(raw_from)
                subject = target_email.get("subject") or "Quick Follow-Up"
                thread_id = target_email.get("threadId") or ""

                # Regenerate reply body with original email context
                tone = (decision or {}).get("tone") or entities.get("tone") or "neutral"
                body_hint = entities.get("body_hint") or ""
                try:
                    reply_data = generate_reply_draft(
                        original_subject=subject,
                        original_body=target_email.get("body") or target_email.get("snippet") or "",
                        original_sender=raw_from,
                        tone=tone,
                        body_hint=body_hint or None,
                    )
                    body = reply_data.get("body") or body_hint or "Thanks for the update."
                except Exception:
                    body = (decision or {}).get("body") or body_hint or "Thanks for the update."

                if not to_email:
                    result = {
                        "status": "needs_clarification",
                        "message": "I found the email, but I couldn't extract the sender email address.",
                    }
                elif not thread_id:
                    result = {
                        "status": "needs_clarification",
                        "message": "I found the email, but I couldn't extract the thread ID.",
                    }
                else:
                    result = create_gmail_reply_draft_service(
                        provider=provider,
                        to=to_email,
                        subject=subject,
                        body=str(body),
                        thread_id=thread_id,
                    )

        elif action in {"reply_and_create_event"}:
            email_reference = entities.get("email_reference") or "latest"
            email_index = entities.get("email_index")
            event_title = (decision or {}).get("event_title") or entities.get("title") or "Meeting"
            event_start = (decision or {}).get("start")
            duration_min = (decision or {}).get("duration_min") or entities.get("duration_min") or 30
            attendee_emails = (
                (decision or {}).get("attendee_emails")
                or entities.get("attendee_emails")
                or []
            )
            attendee_emails = _dedupe_keep_order(attendee_emails)

            has_conflicts = bool((decision or {}).get("has_conflicts", False))
            conflicts = (decision or {}).get("conflicts", []) or []
            alternatives = (decision or {}).get("alternatives", []) or []

            target_email = _resolve_target_email(
                provider=provider,
                email_reference=email_reference,
                email_index=email_index,
            )

            if not target_email:
                result = {
                    "status": "not_found",
                    "message": "No email found to reply to.",
                }
            else:
                raw_from = target_email.get("from") or ""
                to_email = _extract_email_address(raw_from)
                subject = target_email.get("subject") or "Quick Follow-Up"
                thread_id = target_email.get("threadId") or ""

                # Regenerate reply body with original email context
                tone = (decision or {}).get("tone") or entities.get("tone") or "neutral"
                body_hint = entities.get("body_hint") or ""
                try:
                    reply_data = generate_reply_draft(
                        original_subject=subject,
                        original_body=target_email.get("body") or target_email.get("snippet") or "",
                        original_sender=raw_from,
                        tone=tone,
                        body_hint=body_hint or None,
                    )
                    body = reply_data.get("body") or body_hint or "I am available at that time."
                except Exception:
                    body = (decision or {}).get("body") or body_hint or "I am available at that time."

                if not to_email:
                    result = {
                        "status": "needs_clarification",
                        "message": "I found the email, but I couldn't extract the sender email address.",
                    }
                elif not thread_id:
                    result = {
                        "status": "needs_clarification",
                        "message": "I found the email, but I couldn't extract the thread ID.",
                    }
                else:
                    reply_result = create_gmail_reply_draft_service(
                        provider=provider,
                        to=to_email,
                        subject=subject,
                        body=str(body),
                        thread_id=thread_id,
                    )

                    if not event_start:
                        result = {
                            "status": "partial_success",
                            "reply": reply_result,
                            "calendar": {
                                "status": "needs_clarification",
                                "message": "Reply draft created, but I could not determine the event start time.",
                            },
                        }
                    elif has_conflicts:
                        result = {
                            "status": "partial_success",
                            "reply": reply_result,
                            "calendar": {
                                "status": "conflict_detected",
                                "message": "Reply draft created, but I did not create the calendar event because of a conflict.",
                                "conflicts": conflicts,
                                "alternatives": alternatives,
                                "proposed_event": {
                                    "title": event_title,
                                    "start": event_start,
                                    "duration_min": int(duration_min),
                                    "attendee_emails": attendee_emails,
                                },
                            },
                            "message": "Reply draft created. Calendar event not created because of a conflict.",
                        }
                    else:
                        calendar_result = create_event_service(
                            provider=provider,
                            title=str(event_title),
                            start=str(event_start),
                            duration_min=int(duration_min),
                            attendees=attendee_emails,
                        )

                        result = {
                            "status": "success",
                            "reply": reply_result,
                            "calendar": calendar_result,
                            "message": "Reply draft and calendar event created successfully.",
                        }

        elif action in {"draft_email_and_create_event"}:
            recipient = (decision or {}).get("recipient") or entities.get("recipient")
            subject = (decision or {}).get("subject") or entities.get("subject") or "Meeting"
            body = (decision or {}).get("body") or entities.get("body_hint") or ""
            event_title = (decision or {}).get("event_title") or entities.get("title") or "Meeting"
            event_start = (decision or {}).get("start")
            duration_min = (decision or {}).get("duration_min") or entities.get("duration_min") or 30
            attendee_emails = (
                (decision or {}).get("attendee_emails")
                or entities.get("attendee_emails")
                or []
            )
            attendee_emails = _dedupe_keep_order(attendee_emails)

            if recipient and recipient not in attendee_emails:
                attendee_emails = _dedupe_keep_order(attendee_emails + [str(recipient)])

            has_conflicts = bool((decision or {}).get("has_conflicts", False))
            conflicts = (decision or {}).get("conflicts", []) or []
            alternatives = (decision or {}).get("alternatives", []) or []

            if not recipient:
                result = {
                    "status": "needs_clarification",
                    "missing": ["recipient"],
                    "message": "I can prepare the draft and calendar event, but I need the recipient email address.",
                    "example": 'Try: "Draft an email to sarah@example.com saying I am available tomorrow at 2pm and create the meeting"',
                }
            elif "@" not in str(recipient):
                result = {
                    "status": "needs_clarification",
                    "missing": ["recipient_email"],
                    "message": f'I understood the recipient as "{recipient}", but I need the full email address.',
                    "example": f'Draft an email to {recipient}@example.com saying I am available tomorrow at 2pm and create the meeting',
                }
            else:
                draft_result = create_gmail_draft_service(
                    provider=provider,
                    to=str(recipient),
                    subject=str(subject),
                    body=str(body),
                )

                if not event_start:
                    result = {
                        "status": "partial_success",
                        "draft": draft_result,
                        "calendar": {
                            "status": "needs_clarification",
                            "message": "Draft created, but I could not determine the event start time.",
                        },
                        "message": "Draft created. Calendar event not created yet.",
                    }
                elif has_conflicts:
                    result = {
                        "status": "partial_success",
                        "draft": draft_result,
                        "calendar": {
                            "status": "conflict_detected",
                            "message": "Draft created, but I did not create the calendar event because of a conflict.",
                            "conflicts": conflicts,
                            "alternatives": alternatives,
                            "proposed_event": {
                                "title": event_title,
                                "start": event_start,
                                "duration_min": int(duration_min),
                                "attendee_emails": attendee_emails,
                            },
                        },
                        "message": "Draft created. Calendar event not created because of a conflict.",
                    }
                else:
                    calendar_result = create_event_service(
                        provider=provider,
                        title=str(event_title),
                        start=str(event_start),
                        duration_min=int(duration_min),
                        attendees=attendee_emails,
                    )

                    result = {
                        "status": "success",
                        "draft": draft_result,
                        "calendar": calendar_result,
                        "message": "Draft email and calendar event created successfully.",
                    }

        else:
            result = {
                "status": "unsupported_action",
                "message": f"Action '{action}' is not supported yet.",
            }

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


# -----------------------
# LEGACY ROUTES
# -----------------------

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
            "attendee_emails": req.attendee_emails,
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
        f"I hope you're doing well. I'm reaching out regarding {topic}. "
        f"Please let me know the best next step, and if you'd like, I can share any additional details.\n\n"
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