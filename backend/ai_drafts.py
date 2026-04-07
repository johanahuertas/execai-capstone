# backend/ai_drafts.py

import os
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_client = None
try:
    if os.getenv("GROQ_API_KEY"):
        from openai import OpenAI
        _client = OpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )
except Exception:
    _client = None

_DEFAULT_MODEL = os.getenv("AI_MODEL", "llama-3.3-70b-versatile")


def generate_email_draft(
    recipient: str,
    topic: Optional[str] = None,
    tone: str = "professional",
    body_hint: Optional[str] = None,
    subject: Optional[str] = None,
) -> dict:
    if _client:
        try:
            return _generate_with_ai(
                recipient=recipient,
                topic=topic,
                tone=tone,
                body_hint=body_hint,
                subject=subject,
            )
        except Exception:
            pass

    return _generate_with_template(
        recipient=recipient,
        topic=topic,
        tone=tone,
        body_hint=body_hint,
        subject=subject,
    )


def generate_reply_draft(
    original_subject: Optional[str] = None,
    original_body: Optional[str] = None,
    original_sender: Optional[str] = None,
    tone: str = "neutral",
    body_hint: Optional[str] = None,
) -> dict:
    if _client:
        try:
            return _generate_reply_with_ai(
                original_subject=original_subject,
                original_body=original_body,
                original_sender=original_sender,
                tone=tone,
                body_hint=body_hint,
            )
        except Exception:
            pass

    return _generate_reply_with_template(
        tone=tone,
        body_hint=body_hint,
        original_subject=original_subject,
        original_sender=original_sender,
    )


# -----------------------
# AI GENERATION
# -----------------------

def _generate_with_ai(
    recipient: str,
    topic: Optional[str],
    tone: str,
    body_hint: Optional[str],
    subject: Optional[str],
) -> dict:
    system_prompt = (
        "You are an executive assistant that writes professional emails.\n"
        "Write a concise, well-structured email based on the details provided.\n"
        "Return ONLY the email body text — no subject line, no metadata.\n"
        "Do not include 'Subject:' or 'To:' headers in your response.\n"
        "Keep the email brief (3-6 sentences) unless more detail is needed.\n"
        "Match the requested tone exactly."
    )

    user_prompt = "Write an email with the following details:\n"
    user_prompt += f"- Recipient: {recipient}\n"
    user_prompt += f"- Tone: {tone}\n"

    if topic:
        user_prompt += f"- Topic: {topic}\n"
    if body_hint:
        user_prompt += f"- Key message to include: {body_hint}\n"
    if subject:
        user_prompt += f"- Subject context: {subject}\n"

    resp = _client.chat.completions.create(
        model=_DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=500,
    )

    body = (resp.choices[0].message.content or "").strip()

    if not subject and topic:
        subject = f"Regarding {topic.title()}"
    elif not subject:
        subject = "Quick Follow-Up"

    return {"subject": subject, "body": body, "tone": tone, "source": "ai_groq"}


def _generate_reply_with_ai(
    original_subject: Optional[str],
    original_body: Optional[str],
    original_sender: Optional[str],
    tone: str,
    body_hint: Optional[str],
) -> dict:
    system_prompt = (
        "You are an executive assistant that writes email replies.\n"
        "Write a concise, contextual reply based on the original email provided.\n"
        "Return ONLY the reply body text — no subject line, no metadata, no headers.\n"
        "Do not start with 'Subject:', 'To:', or 'Re:'.\n"
        "Keep the reply brief (2-5 sentences).\n"
        "If no original email is provided, write a polite, generic acknowledgment.\n"
        "Match the requested tone exactly."
    )

    # ✅ FIX: build a rich prompt using all available email context
    user_prompt = "Write a reply to this email:\n\n"

    if original_sender:
        user_prompt += f"From: {original_sender}\n"
    if original_subject:
        user_prompt += f"Subject: {original_subject}\n"
    if original_body:
        truncated = original_body[:800] + ("..." if len(original_body) > 800 else "")
        user_prompt += f"\nOriginal message:\n{truncated}\n"
    else:
        user_prompt += "\n(Original message not available — write a polite acknowledgment)\n"

    user_prompt += f"\nTone: {tone}\n"

    if body_hint:
        user_prompt += f"Key message to include in the reply: {body_hint}\n"

    resp = _client.chat.completions.create(
        model=_DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=400,
    )

    body = (resp.choices[0].message.content or "").strip()
    return {"body": body, "tone": tone, "source": "ai_groq"}


# -----------------------
# TEMPLATE FALLBACK
# -----------------------

def _generate_with_template(
    recipient: str,
    topic: Optional[str],
    tone: str,
    body_hint: Optional[str],
    subject: Optional[str],
) -> dict:
    topic = topic or "your request"
    recipient = recipient or "there"

    if not subject:
        subject = f"Regarding {topic.title()}" if topic != "your request" else "Quick Follow-Up"

    if body_hint:
        body = body_hint
    elif tone == "friendly":
        body = (
            f"Hi,\n\n"
            f"I hope you're doing well! I'm reaching out regarding {topic}. "
            f"Let me know the best next step.\n\n"
            f"Thanks,"
        )
    else:
        body = (
            f"Hello,\n\n"
            f"I hope this message finds you well. I am writing regarding {topic}. "
            f"Please let me know how you would like to proceed.\n\n"
            f"Best regards,"
        )

    return {"subject": subject, "body": body, "tone": tone, "source": "template"}


def _generate_reply_with_template(
    tone: str,
    body_hint: Optional[str],
    original_subject: Optional[str] = None,
    original_sender: Optional[str] = None,
) -> dict:
    # ✅ FIX: use original email context in template too when available
    subject_ref = f" regarding '{original_subject}'" if original_subject else ""
    sender_ref = original_sender.split("<")[0].strip() if original_sender else ""
    greeting = f"Hi {sender_ref}," if sender_ref else "Hi,"

    if body_hint:
        if tone == "professional":
            body = f"Hello,\n\n{body_hint}\n\nPlease let me know if you have any questions.\n\nBest regards,"
        else:
            body = f"{greeting}\n\n{body_hint}\n\nThanks!"
    elif tone == "professional":
        body = (
            f"Hello,\n\n"
            f"Thank you for your message{subject_ref}. "
            f"I appreciate you reaching out and will follow up shortly.\n\n"
            f"Best regards,"
        )
    elif tone == "friendly":
        body = (
            f"{greeting}\n\n"
            f"Thanks for the update{subject_ref}! "
            f"I'll get back to you soon.\n\nThanks!"
        )
    else:
        body = (
            f"{greeting}\n\n"
            f"Thank you for your email{subject_ref}. "
            f"I'll review this and respond shortly.\n\nBest,"
        )

    return {"body": body, "tone": tone, "source": "template"}