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
    resolve_contact_name,
)
import re as _re

EMAIL_REGEX = r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"
from .ai_drafts import generate_reply_draft, generate_email_draft

app = FastAPI(title="ExecAI Backend")
app.include_router(integrations_router)


# -----------------------
# MODELS
# -----------------------

class ParseIntentRequest(BaseModel):
    text: str
    provider: Optional[str] = "google"
    # ✅ NEW: contexto de la última acción para follow-ups
    last_context: Optional[Dict[str, Any]] = None


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


def _build_contextual_reply_body(
    target_email: Dict[str, Any],
    body_hint: Optional[str],
    tone: str,
) -> str:
    original_subject = target_email.get("subject") or ""
    original_body = target_email.get("body") or target_email.get("snippet") or ""
    original_sender = target_email.get("from") or ""

    result = generate_reply_draft(
        original_subject=original_subject,
        original_body=original_body,
        original_sender=original_sender,
        tone=tone,
        body_hint=body_hint,
    )
    return result.get("body") or body_hint or "Thanks for the update."


# -----------------------
# ✅ NEW: FOLLOW-UP HANDLER
# -----------------------

def _detect_followup_tone(text: str) -> Optional[str]:
    t = text.lower()
    if any(w in t for w in ["professional", "formal", "seriously", "serious"]):
        return "professional"
    if any(w in t for w in ["friendly", "casual", "nice", "nicer", "warm", "warmer", "relaxed"]):
        return "friendly"
    if any(w in t for w in ["short", "shorter", "brief", "concise", "simpler"]):
        return "concise"
    return None


def _extract_exact_text(text: str) -> Optional[str]:
    """Extract text inside quotes — user wants this exact content."""
    m = _re.search(r'"([^"]+)"', text)
    if m:
        return m.group(1).strip()
    m2 = _re.search(r"'([^']+)'", text)
    if m2 and len(m2.group(1)) > 3:
        return m2.group(1).strip()
    # "answer this exact ..." or "just say ..." without quotes
    m3 = _re.search(r'\b(?:exact|exactly|just say|just write|just send|just reply)\s*[:\-]?\s*(.+)', text, _re.IGNORECASE)
    if m3:
        return m3.group(1).strip().strip('"').strip("'")
    return None


def _is_followup(text: str, last_context: Optional[Dict[str, Any]]) -> bool:
    """Check if the message looks like a follow-up to the previous action."""
    if not last_context:
        return False
    t = text.lower().strip()
    followup_signals = [
        "make it", "change it", "more ", "less ", "too ",
        "not ", "don't like", "didn't like", "i don't",
        "try again", "redo", "rewrite", "rephrase",
        "actually", "instead", "but ", "no,", "nah",
        "shorter", "longer", "nicer", "friendlier",
        "more professional", "more casual", "more formal",
        "add ", "remove ", "include ", "also ",
        "can you", "could you", "please ",
        "that's not", "that doesn't", "wrong ",
        "answer ", "just say", "just write", "just send",
        "say this", "write this", "send this", "exact",
    ]
    return any(signal in t for signal in followup_signals)


def _handle_followup(
    text: str,
    last_context: Dict[str, Any],
    provider: str,
) -> Optional[Dict[str, Any]]:
    """Handle follow-up messages based on the last action's context."""

    last_action = (last_context.get("action") or "").lower()
    last_result = last_context.get("result") or {}
    last_decision = last_context.get("decision") or {}

    # --- Follow-up on DRAFT ---
    if last_action in {"create_draft", "draft_email"}:
        prev_email = last_result.get("email") or {}
        recipient = prev_email.get("to") or ""
        subject = prev_email.get("subject") or "Quick Follow-Up"
        prev_body = prev_email.get("body") or ""

        # ✅ NEW: detect if user wants to change recipient
        new_email_match = _re.search(EMAIL_REGEX, text)
        if new_email_match:
            recipient = new_email_match.group(0).lower()
        else:
            # check for name-based recipient change: "send it to Sarah instead"
            name_match = _re.search(r"\bto\s+([A-Z][a-z]+)\b", text)
            if name_match:
                name = name_match.group(1)
                resolved = resolve_contact_name(provider, name)
                if resolved:
                    recipient = resolved

        new_tone = _detect_followup_tone(text) or "professional"

        # ✅ NEW: if user provides exact text in quotes, use it directly
        exact_text = _extract_exact_text(text)
        if exact_text:
            new_body = exact_text
        else:
            # Use AI to regenerate with the follow-up instruction
            ai_result = generate_email_draft(
                recipient=recipient,
                topic=subject,
                tone=new_tone,
                body_hint=f"Previous draft:\n{prev_body}\n\nUser feedback: {text}",
                subject=subject,
            )
            new_body = ai_result.get("body") or prev_body

        if recipient and "@" in recipient:
            result = create_gmail_draft_service(
                provider=provider,
                to=recipient,
                subject=subject,
                body=new_body,
            )
        else:
            result = {
                "status": "draft_created",
                "draft": {"id": "followup", "messageId": "followup", "threadId": "", "labelIds": []},
                "email": {"to": recipient, "subject": subject, "body": new_body},
            }

        return {
            "intent_data": {"intent": "followup_draft", "entities": {}, "mode": "followup", "original_text": text},
            "decision": {"action": "create_draft", "message": "Here's the updated draft."},
            "result": result,
        }

    # --- Follow-up on REPLY ---
    if last_action in {"reply_email", "create_reply_draft"}:
        prev_email = last_result.get("email") or {}
        to = prev_email.get("to") or ""
        subject = prev_email.get("subject") or "Quick Follow-Up"
        prev_body = prev_email.get("body") or ""
        thread_id = (last_result.get("draft") or {}).get("threadId") or ""

        new_tone = _detect_followup_tone(text) or "neutral"

        # ✅ NEW: if user provides exact text in quotes, use it directly
        exact_text = _extract_exact_text(text)
        if exact_text:
            new_body = exact_text
        else:
            ai_result = generate_reply_draft(
                original_subject=subject,
                original_body=None,
                original_sender=to,
                tone=new_tone,
                body_hint=f"Previous reply:\n{prev_body}\n\nUser feedback: {text}",
            )
            new_body = ai_result.get("body") or prev_body

        if to and thread_id:
            result = create_gmail_reply_draft_service(
                provider=provider,
                to=to,
                subject=subject,
                body=new_body,
                thread_id=thread_id,
            )
        else:
            result = {
                "status": "reply_draft_created",
                "draft": {"id": "followup", "messageId": "followup", "threadId": thread_id, "labelIds": []},
                "email": {"to": to, "subject": subject, "body": new_body},
            }

        return {
            "intent_data": {"intent": "followup_reply", "entities": {}, "mode": "followup", "original_text": text},
            "decision": {"action": "reply_email", "message": "Here's the updated reply."},
            "result": result,
        }

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

    request_provider = (payload.provider or "google").strip().lower()
    last_context = payload.last_context

    # ✅ NEW: Check for follow-up BEFORE normal intent parsing
    if last_context and _is_followup(text, last_context):
        try:
            followup_result = _handle_followup(text, last_context, request_provider)
            if followup_result:
                return followup_result
        except Exception:
            pass  # Fall through to normal flow if follow-up fails

    intent_data = parse_intent_ai(text)
    decision = handle_intent(intent_data)

    # ✅ NEW: If intent is unknown but we have context, try follow-up as fallback
    intent = (intent_data.get("intent") or "").strip()
    if intent == "unknown" and last_context:
        try:
            followup_result = _handle_followup(text, last_context, request_provider)
            if followup_result:
                return followup_result
        except Exception:
            pass

    existing_result = (decision or {}).get("result")
    if existing_result is not None:
        return {
            "intent_data": intent_data,
            "decision": decision,
            "result": existing_result,
        }

    entities: Dict[str, Any] = (intent_data or {}).get("entities") or {}
    action = ((decision or {}).get("action") or (intent_data or {}).get("intent") or "").lower().strip()
    provider = request_provider

    result = None

    try:
        if action in {"list_events", "calendar_list", "get_events"}:
            days = (decision or {}).get("days")
            if days is None:
                days = entities.get("days", 7)
            timeframe = (entities.get("timeframe") or "").lower()
            if timeframe == "today":
                from zoneinfo import ZoneInfo
                from datetime import timezone
                tz = ZoneInfo("America/New_York")
                local_now = datetime.now(tz)
                start_of_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
                end_of_day = local_now.replace(hour=23, minute=59, second=59, microsecond=0)
                result = list_events_service(
                    provider=provider,
                    days=1,
                    time_min_override=start_of_day.isoformat(),
                    time_max_override=end_of_day.isoformat(),
                )
            else:
                result = list_events_service(provider=provider, days=int(days))

        elif action in {"create_event", "calendar_create", "schedule_event"}:
            title = (decision or {}).get("title") or entities.get("title")
            start = (decision or {}).get("start") or entities.get("start")
            duration_min = (decision or {}).get("duration_min") or entities.get("duration_min") or 30
            attendee_emails = _dedupe_keep_order(
                (decision or {}).get("attendee_emails") or entities.get("attendee_emails") or []
            )
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
                result = {
                    "status": "pending_confirmation",
                    "provider": provider,
                    "title": title,
                    "start": start,
                    "duration_min": int(duration_min),
                    "attendee_emails": attendee_emails,
                    "message": "Ready to create this event. Confirm to proceed.",
                }

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
            max_results = (decision or {}).get("max_results") or entities.get("max_results", 5)
            result = list_emails_service(
                provider=provider,
                max_results=int(max_results),
                inbox_only=True,
                primary_only=False,
            )

        elif action in {"read_email", "open_email"}:
            email_reference = (decision or {}).get("email_reference") or entities.get("email_reference") or "latest"
            email_index = (decision or {}).get("email_index") or entities.get("email_index")

            target_email = _resolve_target_email(
                provider=provider,
                email_reference=email_reference,
                email_index=email_index,
            )

            if not target_email:
                result = {"status": "not_found", "message": "I couldn't find the requested email."}
            else:
                result = {"provider": provider, "email": target_email}

        elif action in {"create_draft", "draft_email"}:
            recipient = (decision or {}).get("recipient") or entities.get("recipient")
            subject = (decision or {}).get("subject") or entities.get("subject") or "Quick Follow-Up"
            body = (decision or {}).get("body") or entities.get("body_hint") or ""

            # ✅ NEW: auto-resolve name to email from contacts
            if recipient and "@" not in str(recipient):
                resolved = resolve_contact_name(provider, str(recipient))
                if resolved:
                    recipient = resolved

            if not recipient:
                result = {
                    "status": "needs_clarification",
                    "missing": ["recipient"],
                    "message": "I can create the draft, but I need the recipient email address.",
                    "example": 'Try: "Draft an email to sarah@example.com about the proposal"',
                }
            elif "@" not in str(recipient):
                # ✅ NEW: show contact suggestions when name not found
                from .integrations import search_contacts_service
                # first try exact name match
                suggestions = search_contacts_service(provider, str(recipient), max_scan=30)
                if suggestions:
                    suggestion_list = ", ".join(f"{c['name']} ({c['email']})" for c in suggestions[:5])
                    result = {
                        "status": "needs_clarification",
                        "missing": ["recipient_email"],
                        "message": f'I found contacts matching "{recipient}": {suggestion_list}. Which one? You can say the full email or just the name.',
                        "suggestions": suggestions[:5],
                    }
                else:
                    # no match — show recent contacts as options
                    all_contacts = search_contacts_service(provider, "", max_scan=30)
                    if all_contacts:
                        contact_list = ", ".join(f"{c['name']} ({c['email']})" for c in all_contacts[:5])
                        result = {
                            "status": "needs_clarification",
                            "missing": ["recipient_email"],
                            "message": f'I couldn\'t find "{recipient}" in your contacts. Here are some recent contacts: {contact_list}. Try the full email address.',
                            "suggestions": all_contacts[:5],
                        }
                    else:
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
            email_index = (decision or {}).get("email_index") or entities.get("email_index")
            body_hint = entities.get("body_hint") or ""
            tone = entities.get("tone") or "neutral"

            target_email = _resolve_target_email(
                provider=provider,
                email_reference=email_reference,
                email_index=email_index,
            )

            if not target_email:
                result = {"status": "not_found", "message": "No email found to reply to."}
            else:
                raw_from = target_email.get("from") or ""
                to_email = _extract_email_address(raw_from)
                subject = target_email.get("subject") or "Quick Follow-Up"
                thread_id = target_email.get("threadId") or ""

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
                    body = _build_contextual_reply_body(target_email, body_hint, tone)
                    result = create_gmail_reply_draft_service(
                        provider=provider,
                        to=to_email,
                        subject=subject,
                        body=body,
                        thread_id=thread_id,
                    )

        elif action in {"reply_and_create_event"}:
            email_reference = entities.get("email_reference") or "latest"
            email_index = entities.get("email_index")
            body_hint = entities.get("body_hint") or ""
            tone = entities.get("tone") or "neutral"
            event_title = (decision or {}).get("event_title") or entities.get("title") or "Meeting"
            event_start = (decision or {}).get("start")
            duration_min = (decision or {}).get("duration_min") or entities.get("duration_min") or 30
            attendee_emails = _dedupe_keep_order(
                (decision or {}).get("attendee_emails") or entities.get("attendee_emails") or []
            )
            has_conflicts = bool((decision or {}).get("has_conflicts", False))
            conflicts = (decision or {}).get("conflicts", []) or []
            alternatives = (decision or {}).get("alternatives", []) or []

            target_email = _resolve_target_email(
                provider=provider,
                email_reference=email_reference,
                email_index=email_index,
            )

            if not target_email:
                result = {"status": "not_found", "message": "No email found to reply to."}
            else:
                raw_from = target_email.get("from") or ""
                to_email = _extract_email_address(raw_from)
                subject = target_email.get("subject") or "Quick Follow-Up"
                thread_id = target_email.get("threadId") or ""

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
                    if has_conflicts:
                        pending_body = _build_contextual_reply_body(target_email, body_hint, tone)
                        result = {
                            "status": "partial_success",
                            "reply": None,
                            "pending_reply": {
                                "to": to_email,
                                "subject": subject,
                                "body": pending_body,
                                "thread_id": thread_id,
                            },
                            "calendar": {
                                "status": "conflict_detected",
                                "message": "I found a conflict at that time. Pick an alternative time before I create the reply and event.",
                                "conflicts": conflicts,
                                "alternatives": alternatives,
                                "proposed_event": {
                                    "title": event_title,
                                    "start": event_start,
                                    "duration_min": int(duration_min),
                                    "attendee_emails": attendee_emails,
                                },
                            },
                            "message": "Conflict detected — choose an alternative time first.",
                        }
                    elif not event_start:
                        body = _build_contextual_reply_body(target_email, body_hint, tone)
                        reply_result = create_gmail_reply_draft_service(
                            provider=provider,
                            to=to_email,
                            subject=subject,
                            body=body,
                            thread_id=thread_id,
                        )
                        result = {
                            "status": "partial_success",
                            "reply": reply_result,
                            "calendar": {
                                "status": "needs_clarification",
                                "message": "Reply draft created, but I could not determine the event start time.",
                            },
                        }
                    else:
                        body = _build_contextual_reply_body(target_email, body_hint, tone)
                        reply_result = create_gmail_reply_draft_service(
                            provider=provider,
                            to=to_email,
                            subject=subject,
                            body=body,
                            thread_id=thread_id,
                        )
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
            attendee_emails = _dedupe_keep_order(
                (decision or {}).get("attendee_emails") or entities.get("attendee_emails") or []
            )
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
        result = {"status": "error", "where": "integrations", "detail": e.detail}
    except Exception as e:
        result = {"status": "error", "where": "assistant", "detail": f"{type(e).__name__}: {str(e)}"}

    return {"intent_data": intent_data, "decision": decision, "result": result}


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
    return {"intent": "meeting_scheduling", "options": options, "provider": "mock"}


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
        f"Please let me know the best next step.\n\n"
        f"{closing}"
    )

    return {
        "status": "drafted",
        "email": {"to": recipient, "subject": subject, "body": body, "tone": tone, "provider": "mock"},
        "message": "Email draft generated (mock).",
    }