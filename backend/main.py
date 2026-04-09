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
    search_contacts_service,
)
import re as _re

EMAIL_REGEX = r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"

from .ai_drafts import (
    generate_reply_draft,
    generate_email_draft,
    revise_email_draft,
)

app = FastAPI(title="ExecAI Backend")
app.include_router(integrations_router)


# -----------------------
# MODELS
# -----------------------

class ParseIntentRequest(BaseModel):
    text: str
    provider: Optional[str] = "google"
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


def _find_recent_real_contacts(provider: str, query: str = "", max_scan: int = 30) -> List[Dict[str, Any]]:
    suggestions = search_contacts_service(provider, query, max_scan=max_scan)
    real_contacts = [
        c for c in suggestions
        if not any(
            skip in c["email"].lower() for skip in [
                "noreply", "no-reply", "mailer-daemon", "postmaster",
                "notifications", "newsletter", "marketing", "promo",
                "factory", "store", "shop", "sales@", "support@", "info@",
            ]
        )
    ]
    return real_contacts


def _extract_exact_text(text: str) -> Optional[str]:
    m = _re.search(r'"([^"]+)"', text)
    if m:
        return m.group(1).strip()

    m2 = _re.search(r"'([^']+)'", text)
    if m2 and len(m2.group(1)) > 1:
        return m2.group(1).strip()

    m3 = _re.search(
        r'\b(?:exact|exactly|just say|just write|just send|just reply|say this|write this|send this)\s*[:\-]?\s*(.+)',
        text,
        _re.IGNORECASE,
    )
    if m3:
        return m3.group(1).strip().strip('"').strip("'")

    return None


def _extract_previous_email_payload(last_result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(last_result, dict):
        return {}

    email = last_result.get("email")
    if isinstance(email, dict) and email:
        return email

    draft = last_result.get("draft")
    if isinstance(draft, dict):
        email_from_draft = draft.get("email")
        if isinstance(email_from_draft, dict) and email_from_draft:
            return email_from_draft

    reply = last_result.get("reply")
    if isinstance(reply, dict):
        reply_email = reply.get("email")
        if isinstance(reply_email, dict) and reply_email:
            return reply_email

    nested_draft = last_result.get("draft")
    if isinstance(nested_draft, dict):
        nested_email = nested_draft.get("email")
        if isinstance(nested_email, dict) and nested_email:
            return nested_email

    return {}


def _extract_previous_draft_meta(last_result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(last_result, dict):
        return {}

    draft = last_result.get("draft")
    if isinstance(draft, dict) and draft:
        return draft

    reply = last_result.get("reply")
    if isinstance(reply, dict):
        draft2 = reply.get("draft")
        if isinstance(draft2, dict) and draft2:
            return draft2

    return {}


# -----------------------
# FOLLOW-UP DETECTION
# -----------------------

def _is_followup(text: str, last_context: Optional[Dict[str, Any]]) -> bool:
    if not last_context:
        return False

    t = (text or "").lower().strip()
    if not t:
        return False

    last_result = last_context.get("result") or {}
    if last_result.get("status") == "needs_clarification":
        if _re.search(EMAIL_REGEX, text):
            return True

    followup_signals = [
        "make it", "change it", "more ", "less ", "too ",
        "not ", "don't like", "didn't like", "i don't",
        "try again", "redo", "rewrite", "rephrase", "revise",
        "actually", "instead", "but ", "no,", "nah",
        "shorter", "longer", "nicer", "friendlier", "warmer",
        "more professional", "more casual", "more formal",
        "add ", "remove ", "include ", "also ", "mention ",
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
    last_action = (last_context.get("action") or "").lower()
    last_result = last_context.get("result") or {}
    last_decision = last_context.get("decision") or {}

    # --- clarification follow-up ---
    if last_result.get("status") == "needs_clarification":
        email_match = _re.search(EMAIL_REGEX, text)
        if email_match and last_action in {"create_draft", "draft_email", "draft_email_and_create_event"}:
            new_recipient = email_match.group(0).lower()
            subject = last_decision.get("subject") or "Quick Follow-Up"
            body = last_decision.get("body") or ""

            if not body:
                body_result = generate_email_draft(
                    recipient=new_recipient,
                    topic=subject,
                    tone=last_decision.get("tone") or "professional",
                    subject=subject,
                )
                body = body_result.get("body") or ""

            result = create_gmail_draft_service(
                provider=provider,
                to=new_recipient,
                subject=subject,
                body=body,
            )
            return {
                "intent_data": {
                    "intent": "followup_clarification",
                    "entities": {"recipient": new_recipient},
                    "mode": "followup",
                    "original_text": text,
                },
                "decision": {
                    "action": "create_draft",
                    "message": f"Draft created for {new_recipient}.",
                },
                "result": result,
            }

    # --- follow-up on draft email ---
    if last_action in {"create_draft", "draft_email", "draft_email_and_create_event"}:
        prev_email = _extract_previous_email_payload(last_result)
        recipient = prev_email.get("to") or ""
        subject = prev_email.get("subject") or "Quick Follow-Up"
        prev_body = prev_email.get("body") or ""

        new_email_match = _re.search(EMAIL_REGEX, text)
        if new_email_match:
            recipient = new_email_match.group(0).lower()
        else:
            name_match = _re.search(r"\bto\s+([A-Z][a-z]+)\b", text)
            if name_match:
                name = name_match.group(1)
                resolved = resolve_contact_name(provider, name)
                if resolved:
                    recipient = resolved

        exact_text = _extract_exact_text(text)
        if exact_text:
            new_body = exact_text
        else:
            revised = revise_email_draft(
                current_body=prev_body,
                revision_instruction=text,
                subject=subject,
                recipient=recipient,
                original_context=last_decision.get("message") or "",
                tone=last_decision.get("tone") or "professional",
            )
            new_body = revised.get("body") or prev_body

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
                "draft": {
                    "id": "followup",
                    "messageId": "followup",
                    "threadId": "",
                    "labelIds": [],
                },
                "email": {
                    "to": recipient,
                    "subject": subject,
                    "body": new_body,
                },
            }

        return {
            "intent_data": {
                "intent": "revise_draft",
                "entities": {"revision_instruction": text},
                "mode": "followup",
                "original_text": text,
            },
            "decision": {
                "action": "create_draft",
                "message": "Here's the updated draft.",
                "tone": last_decision.get("tone") or "professional",
            },
            "result": result,
        }

    # --- follow-up on reply draft ---
    if last_action in {"reply_email", "create_reply_draft", "reply_and_create_event"}:
        prev_email = _extract_previous_email_payload(last_result)
        prev_draft = _extract_previous_draft_meta(last_result)

        to_email = prev_email.get("to") or ""
        subject = prev_email.get("subject") or "Quick Follow-Up"
        prev_body = prev_email.get("body") or ""
        thread_id = prev_draft.get("threadId") or ""

        exact_text = _extract_exact_text(text)
        if exact_text:
            new_body = exact_text
        else:
            revised = revise_email_draft(
                current_body=prev_body,
                revision_instruction=text,
                subject=subject,
                recipient=to_email,
                original_context="Reply draft revision",
                tone=last_decision.get("tone") or "neutral",
            )
            new_body = revised.get("body") or prev_body

        if to_email and thread_id:
            result = create_gmail_reply_draft_service(
                provider=provider,
                to=to_email,
                subject=subject,
                body=new_body,
                thread_id=thread_id,
            )
        else:
            result = {
                "status": "reply_draft_created",
                "draft": {
                    "id": "followup",
                    "messageId": "followup",
                    "threadId": thread_id,
                    "labelIds": [],
                },
                "email": {
                    "to": to_email,
                    "subject": subject,
                    "body": new_body,
                },
            }

        return {
            "intent_data": {
                "intent": "revise_reply_draft",
                "entities": {"revision_instruction": text},
                "mode": "followup",
                "original_text": text,
            },
            "decision": {
                "action": "reply_email",
                "message": "Here's the updated reply.",
                "tone": last_decision.get("tone") or "neutral",
            },
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
    return parse_intent_ai(text, payload.last_context)


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

    if last_context and _is_followup(text, last_context):
        try:
            followup_result = _handle_followup(text, last_context, request_provider)
            if followup_result:
                return followup_result
        except Exception as e:
            print("followup failed:", type(e).__name__, str(e))

    intent_data = parse_intent_ai(text, last_context)
    decision = handle_intent(intent_data)

    intent = (intent_data.get("intent") or "").strip()
    if intent == "unknown" and last_context:
        try:
            followup_result = _handle_followup(text, last_context, request_provider)
            if followup_result:
                return followup_result
        except Exception as e:
            print("followup fallback failed:", type(e).__name__, str(e))

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
            tone = (decision or {}).get("tone") or entities.get("tone") or "professional"

            if not recipient:
                result = {
                    "status": "needs_clarification",
                    "missing": ["recipient"],
                    "message": "I can create the draft, but I need the recipient email address.",
                    "example": 'Try: "Draft an email to sarah@example.com about the proposal"',
                }
            elif "@" not in str(recipient):
                real_contacts = _find_recent_realContacts = _find_recent_real_contacts(provider, str(recipient), max_scan=30)

                if real_contacts:
                    suggestion_list = "\n".join(f"• {c['name']} — {c['email']}" for c in real_contacts[:5])
                    result = {
                        "status": "needs_clarification",
                        "missing": ["recipient_email"],
                        "message": f'I found contacts matching "{recipient}":\n{suggestion_list}\n\nWhich one? Reply with the full email address.',
                        "suggestions": real_contacts[:5],
                    }
                else:
                    real_all = _find_recent_real_contacts(provider, "", max_scan=30)
                    if real_all:
                        contact_list = "\n".join(f"• {c['name']} — {c['email']}" for c in real_all[:5])
                        result = {
                            "status": "needs_clarification",
                            "missing": ["recipient_email"],
                            "message": f'I couldn\'t find "{recipient}" in your contacts. Here are your recent contacts:\n{contact_list}\n\nReply with the full email address.',
                            "suggestions": real_all[:5],
                        }
                    else:
                        result = {
                            "status": "needs_clarification",
                            "missing": ["recipient_email"],
                            "message": f'I understood the recipient as "{recipient}", but I need the full email address.',
                            "example": f'Try: "Draft an email to {recipient}@example.com about the proposal"',
                        }
            else:
                if not body:
                    generated = generate_email_draft(
                        recipient=str(recipient),
                        topic=str(subject),
                        tone=tone,
                        subject=str(subject),
                    )
                    body = generated.get("body") or ""

                result = create_gmail_draft_service(
                    provider=provider,
                    to=str(recipient),
                    subject=str(subject),
                    body=str(body),
                )

        elif action == "revise_draft":
            if not last_context:
                result = {
                    "status": "needs_clarification",
                    "message": "I need a previous draft to revise.",
                }
            else:
                prev_result = last_context.get("result") or {}
                prev_email = _extract_previous_email_payload(prev_result)

                recipient = prev_email.get("to") or ""
                subject = prev_email.get("subject") or "Quick Follow-Up"
                prev_body = prev_email.get("body") or ""
                revision_instruction = entities.get("revision_instruction") or text
                tone = (decision or {}).get("tone") or entities.get("tone") or "professional"

                exact_text = _extract_exact_text(text)
                if exact_text:
                    new_body = exact_text
                else:
                    revised = revise_email_draft(
                        current_body=prev_body,
                        revision_instruction=revision_instruction,
                        subject=subject,
                        recipient=recipient,
                        original_context="Draft revision",
                        tone=tone,
                    )
                    new_body = revised.get("body") or prev_body

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
                        "draft": {
                            "id": "revised",
                            "messageId": "revised",
                            "threadId": "",
                            "labelIds": [],
                        },
                        "email": {
                            "to": recipient,
                            "subject": subject,
                            "body": new_body,
                        },
                    }

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

        elif action == "revise_reply_draft":
            if not last_context:
                result = {
                    "status": "needs_clarification",
                    "message": "I need a previous reply draft to revise.",
                }
            else:
                prev_result = last_context.get("result") or {}
                prev_email = _extract_previous_email_payload(prev_result)
                prev_draft = _extract_previous_draft_meta(prev_result)

                to_email = prev_email.get("to") or ""
                subject = prev_email.get("subject") or "Quick Follow-Up"
                prev_body = prev_email.get("body") or ""
                thread_id = prev_draft.get("threadId") or ""
                revision_instruction = entities.get("revision_instruction") or text
                tone = (decision or {}).get("tone") or entities.get("tone") or "neutral"

                exact_text = _extract_exact_text(text)
                if exact_text:
                    new_body = exact_text
                else:
                    revised = revise_email_draft(
                        current_body=prev_body,
                        revision_instruction=revision_instruction,
                        subject=subject,
                        recipient=to_email,
                        original_context="Reply draft revision",
                        tone=tone,
                    )
                    new_body = revised.get("body") or prev_body

                if to_email and thread_id:
                    result = create_gmail_reply_draft_service(
                        provider=provider,
                        to=to_email,
                        subject=subject,
                        body=new_body,
                        thread_id=thread_id,
                    )
                else:
                    result = {
                        "status": "reply_draft_created",
                        "draft": {
                            "id": "revised_reply",
                            "messageId": "revised_reply",
                            "threadId": thread_id,
                            "labelIds": [],
                        },
                        "email": {
                            "to": to_email,
                            "subject": subject,
                            "body": new_body,
                        },
                    }

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
            tone = (decision or {}).get("tone") or entities.get("tone") or "professional"
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
                if not body:
                    generated = generate_email_draft(
                        recipient=str(recipient),
                        topic=str(subject),
                        tone=tone,
                        subject=str(subject),
                    )
                    body = generated.get("body") or ""

                if not event_start:
                    draft_result = create_gmail_draft_service(
                        provider=provider,
                        to=str(recipient),
                        subject=str(subject),
                        body=str(body),
                    )
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
                        "draft": None,
                        "pending_draft": {
                            "to": str(recipient),
                            "subject": str(subject),
                            "body": str(body),
                        },
                        "calendar": {
                            "status": "conflict_detected",
                            "message": "I found a conflict. Pick an alternative time — I'll create both the draft and event together.",
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
                else:
                    draft_result = create_gmail_draft_service(
                        provider=provider,
                        to=str(recipient),
                        subject=str(subject),
                        body=str(body),
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
        print("assistant failed:", type(e).__name__, str(e))
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

    generated = generate_email_draft(
        recipient=recipient,
        topic=topic,
        tone=tone,
        subject=f"Regarding {topic.title()}" if topic else "Quick Follow-Up",
        body_hint=req.original_text,
    )

    return {
        "status": "drafted",
        "email": {
            "to": recipient,
            "subject": generated.get("subject") or "Quick Follow-Up",
            "body": generated.get("body") or "",
            "tone": tone,
            "provider": "mock",
        },
        "message": "Email draft generated (mock).",
    }