import os
import re
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_openai_client = None
try:
    if os.getenv("OPENAI_API_KEY"):
        from openai import OpenAI
        _openai_client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
        )
except Exception as e:
    print("OpenAI client init failed:", type(e).__name__, str(e))
    _openai_client = None

_groq_client = None
try:
    if os.getenv("GROQ_API_KEY"):
        from openai import OpenAI
        _groq_client = OpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )
except Exception as e:
    print("Groq client init failed:", type(e).__name__, str(e))
    _groq_client = None

_DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-nano")
_DEFAULT_GROQ_MODEL = os.getenv("AI_MODEL", "llama-3.3-70b-versatile")


def _clean_output(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = re.sub(r"^subject\s*:.*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^to\s*:.*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _default_subject(topic: Optional[str], subject: Optional[str]) -> str:
    if subject and subject.strip():
        return subject.strip()
    if topic and topic.strip():
        t = topic.strip()
        return t[:1].upper() + t[1:]
    return "Quick Follow-Up"


def _has_openai() -> bool:
    return _openai_client is not None


def _has_groq() -> bool:
    return _groq_client is not None


def _openai_text(system_prompt: str, user_prompt: str) -> str:
    resp = _openai_client.responses.create(
        model=_DEFAULT_OPENAI_MODEL,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return _clean_output(resp.output_text or "")


def generate_email_draft(
    recipient: str,
    topic: Optional[str] = None,
    tone: str = "professional",
    body_hint: Optional[str] = None,
    subject: Optional[str] = None,
) -> dict:
    if _has_openai():
        try:
            return _generate_with_openai(
                recipient=recipient,
                topic=topic,
                tone=tone,
                body_hint=body_hint,
                subject=subject,
            )
        except Exception as e:
            print("generate_email_draft OpenAI failed:", type(e).__name__, str(e))

    if _has_groq():
        try:
            return _generate_with_groq(
                recipient=recipient,
                topic=topic,
                tone=tone,
                body_hint=body_hint,
                subject=subject,
            )
        except Exception as e:
            print("generate_email_draft Groq failed:", type(e).__name__, str(e))

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
    if _has_openai():
        try:
            return _generate_reply_with_openai(
                original_subject=original_subject,
                original_body=original_body,
                original_sender=original_sender,
                tone=tone,
                body_hint=body_hint,
            )
        except Exception as e:
            print("generate_reply_draft OpenAI failed:", type(e).__name__, str(e))

    if _has_groq():
        try:
            return _generate_reply_with_groq(
                original_subject=original_subject,
                original_body=original_body,
                original_sender=original_sender,
                tone=tone,
                body_hint=body_hint,
            )
        except Exception as e:
            print("generate_reply_draft Groq failed:", type(e).__name__, str(e))

    return _generate_reply_with_template(
        tone=tone,
        body_hint=body_hint,
        original_subject=original_subject,
        original_sender=original_sender,
    )


def revise_email_draft(
    current_body: str,
    revision_instruction: str,
    subject: Optional[str] = None,
    recipient: Optional[str] = None,
    original_context: Optional[str] = None,
    tone: str = "professional",
) -> dict:
    if _has_openai():
        try:
            return _revise_email_with_openai(
                current_body=current_body,
                revision_instruction=revision_instruction,
                subject=subject,
                recipient=recipient,
                original_context=original_context,
                tone=tone,
            )
        except Exception as e:
            print("revise_email_draft OpenAI failed:", type(e).__name__, str(e))

    if _has_groq():
        try:
            return _revise_email_with_groq(
                current_body=current_body,
                revision_instruction=revision_instruction,
                subject=subject,
                recipient=recipient,
                original_context=original_context,
                tone=tone,
            )
        except Exception as e:
            print("revise_email_draft Groq failed:", type(e).__name__, str(e))

    revised = _revise_with_rules(current_body, revision_instruction)
    return {
        "subject": subject or "Quick Follow-Up",
        "body": revised,
        "tone": tone,
        "source": "rule_revision",
    }


def _generate_with_openai(
    recipient: str,
    topic: Optional[str],
    tone: str,
    body_hint: Optional[str],
    subject: Optional[str],
) -> dict:
    subject = _default_subject(topic, subject)

    system_prompt = """
You are an excellent executive assistant writing real workplace emails.

Your job:
- Write a natural, human-sounding email body.
- Be concise, specific, and useful.
- Do not sound robotic, stiff, or overly formal.
- Do not use filler such as:
  "I hope this message finds you well"
  "I am writing regarding"
  "Please let me know how you would like to proceed"
  unless the user clearly wants very formal language.
- Do not invent facts, names, dates, or commitments.
- Use the user's requested message as the highest priority.
- If details are limited, write a sensible short email that still sounds useful.
- Return ONLY the email body text.
- No subject line.
- No metadata.
- No explanations.
"""

    user_prompt = f"""
Write an email.

Recipient: {recipient or "unknown"}
Tone: {tone}
Subject: {subject}
Topic: {topic or ""}
What the user wants to say: {body_hint or ""}

Requirements:
- Keep it brief.
- Make it sound natural.
- Include only concrete details provided above.
- If the details are sparse, do not write something empty or generic.
- Prefer a practical email someone would actually send at work.
"""

    body = _openai_text(system_prompt.strip(), user_prompt.strip())
    return {"subject": subject, "body": body, "tone": tone, "source": "openai"}


def _generate_reply_with_openai(
    original_subject: Optional[str],
    original_body: Optional[str],
    original_sender: Optional[str],
    tone: str,
    body_hint: Optional[str],
) -> dict:
    system_prompt = """
You are an excellent executive assistant writing email replies.

Rules:
- Write a reply that directly responds to the original email.
- Prioritize the user's requested reply intent.
- Keep it short, natural, and specific.
- Do not summarize the original email unless needed.
- Do not sound robotic or overly formal.
- Do not include a subject line, headers, or commentary.
- Return ONLY the reply body.
- Do not include placeholders like [Your Name].
"""

    truncated_original = (original_body or "").strip()
    if len(truncated_original) > 1500:
        truncated_original = truncated_original[:1500] + "..."

    user_prompt = f"""
Write a reply to this email.

From: {original_sender or ""}
Subject: {original_subject or ""}
Original email:
{truncated_original or "(not available)"}

User instruction for the reply:
{body_hint or "(No extra instruction given. Write a polite direct reply.)"}

Tone: {tone}

Requirements:
- Keep it between 1 and 5 short paragraphs.
- If the user's instruction implies a very short answer, keep it very short.
- Do not add made-up details.
"""

    body = _openai_text(system_prompt.strip(), user_prompt.strip())
    return {"body": body, "tone": tone, "source": "openai"}


def _revise_email_with_openai(
    current_body: str,
    revision_instruction: str,
    subject: Optional[str],
    recipient: Optional[str],
    original_context: Optional[str],
    tone: str,
) -> dict:
    system_prompt = """
You are revising an existing email draft.

Rules:
- Rewrite the draft according to the user's revision instruction.
- Preserve the original intent unless the instruction changes it.
- Return ONLY the revised email body.
- Do not explain changes.
- Do not include labels like 'Revised version' or 'User feedback'.
- Do not include headers such as Subject:, To:, or Notes:.
- If the instruction is "shorter", make it noticeably shorter.
- If the instruction is "one line" or "one sentence", return exactly one sentence.
- If the instruction is "longer", add useful detail, not fluff.
- If the instruction asks for a warmer, softer, or more professional tone, adjust the wording naturally.
- If the user says things like "not like that", "less cheesy", or "make it direct", rewrite the whole draft more naturally.
- If the user asks to add something like a date, attachment, or specific phrase, include it cleanly in the revised version.
"""

    user_prompt = f"""
Current draft:
{current_body}

Revision request:
{revision_instruction}

Recipient:
{recipient or ""}

Subject:
{subject or ""}

Original context:
{original_context or ""}

Tone:
{tone}

Return only the revised body.
"""

    body = _openai_text(system_prompt.strip(), user_prompt.strip())
    return {
        "subject": subject or "Quick Follow-Up",
        "body": body,
        "tone": tone,
        "source": "openai_revision",
    }


def _generate_with_groq(
    recipient: str,
    topic: Optional[str],
    tone: str,
    body_hint: Optional[str],
    subject: Optional[str],
) -> dict:
    subject = _default_subject(topic, subject)

    system_prompt = """
You are an excellent executive assistant writing real workplace emails.

Your job:
- Write a natural, human-sounding email body.
- Be concise, specific, and useful.
- Do not sound robotic, stiff, or overly formal.
- Do not use filler such as:
  "I hope this message finds you well"
  "I am writing regarding"
  "Please let me know how you would like to proceed"
  unless the user clearly wants very formal language.
- Do not invent facts, names, dates, or commitments.
- Use the user's requested message as the highest priority.
- If details are limited, write a sensible short email that still sounds useful.
- Return ONLY the email body text.
- No subject line.
- No metadata.
- No explanations.
"""

    user_prompt = f"""
Write an email.

Recipient: {recipient or "unknown"}
Tone: {tone}
Subject: {subject}
Topic: {topic or ""}
What the user wants to say: {body_hint or ""}

Requirements:
- Keep it brief.
- Make it sound natural.
- Include only concrete details provided above.
- If the details are sparse, do not write something empty or generic.
- Prefer a practical email someone would actually send at work.
"""

    resp = _groq_client.chat.completions.create(
        model=_DEFAULT_GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()},
        ],
        temperature=0.35,
        max_tokens=250,
    )

    body = _clean_output(resp.choices[0].message.content or "")
    return {"subject": subject, "body": body, "tone": tone, "source": "ai_groq"}


def _generate_reply_with_groq(
    original_subject: Optional[str],
    original_body: Optional[str],
    original_sender: Optional[str],
    tone: str,
    body_hint: Optional[str],
) -> dict:
    system_prompt = """
You are an excellent executive assistant writing email replies.

Rules:
- Write a reply that directly responds to the original email.
- Prioritize the user's requested reply intent.
- Keep it short, natural, and specific.
- Do not summarize the original email unless needed.
- Do not sound robotic or overly formal.
- Do not include a subject line, headers, or commentary.
- Return ONLY the reply body.
- Do not include placeholders like [Your Name].
"""

    truncated_original = (original_body or "").strip()
    if len(truncated_original) > 1500:
        truncated_original = truncated_original[:1500] + "..."

    user_prompt = f"""
Write a reply to this email.

From: {original_sender or ""}
Subject: {original_subject or ""}
Original email:
{truncated_original or "(not available)"}

User instruction for the reply:
{body_hint or "(No extra instruction given. Write a polite direct reply.)"}

Tone: {tone}

Requirements:
- Keep it between 1 and 5 short paragraphs.
- If the user's instruction implies a very short answer, keep it very short.
- Do not add made-up details.
"""

    resp = _groq_client.chat.completions.create(
        model=_DEFAULT_GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()},
        ],
        temperature=0.3,
        max_tokens=220,
    )

    body = _clean_output(resp.choices[0].message.content or "")
    return {"body": body, "tone": tone, "source": "ai_groq"}


def _revise_email_with_groq(
    current_body: str,
    revision_instruction: str,
    subject: Optional[str],
    recipient: Optional[str],
    original_context: Optional[str],
    tone: str,
) -> dict:
    system_prompt = """
You are revising an existing email draft.

Rules:
- Rewrite the draft according to the user's revision instruction.
- Preserve the original intent unless the instruction changes it.
- Return ONLY the revised email body.
- Do not explain changes.
- Do not include labels like 'Revised version' or 'User feedback'.
- Do not include headers such as Subject:, To:, or Notes:.
- If the instruction is "shorter", make it noticeably shorter.
- If the instruction is "one line" or "one sentence", return exactly one sentence.
- If the instruction is "longer", add useful detail, not fluff.
- If the instruction asks for a warmer, softer, or more professional tone, adjust the wording naturally.
- If the user says things like "not like that", "less cheesy", or "make it direct", rewrite the whole draft more naturally.
- If the user asks to add something like a date, attachment, or specific phrase, include it cleanly in the revised version.
"""

    user_prompt = f"""
Current draft:
{current_body}

Revision request:
{revision_instruction}

Recipient:
{recipient or ""}

Subject:
{subject or ""}

Original context:
{original_context or ""}

Tone:
{tone}

Return only the revised body.
"""

    resp = _groq_client.chat.completions.create(
        model=_DEFAULT_GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()},
        ],
        temperature=0.25,
        max_tokens=260,
    )

    body = _clean_output(resp.choices[0].message.content or "")
    return {
        "subject": subject or "Quick Follow-Up",
        "body": body,
        "tone": tone,
        "source": "ai_groq_revision",
    }


def _generate_with_template(
    recipient: str,
    topic: Optional[str],
    tone: str,
    body_hint: Optional[str],
    subject: Optional[str],
) -> dict:
    subject = _default_subject(topic, subject)

    if body_hint and body_hint.strip():
        body = body_hint.strip()
    else:
        clean_topic = (topic or "this").strip()

        if tone == "friendly":
            body = (
                f"Hi,\n\n"
                f"I wanted to follow up about {clean_topic}. Let me know your thoughts when you have a chance.\n\n"
                f"Best,"
            )
        elif tone == "professional":
            body = (
                f"Hello,\n\n"
                f"I wanted to follow up regarding {clean_topic}. Please let me know your thoughts when you have a chance.\n\n"
                f"Best regards,"
            )
        else:
            body = (
                f"Hi,\n\n"
                f"I wanted to follow up about {clean_topic}. Please let me know what you think.\n\n"
                f"Best,"
            )

    return {"subject": subject, "body": body, "tone": tone, "source": "template"}


def _generate_reply_with_template(
    tone: str,
    body_hint: Optional[str],
    original_subject: Optional[str] = None,
    original_sender: Optional[str] = None,
) -> dict:
    sender_ref = ""
    if original_sender:
        sender_ref = original_sender.split("<")[0].strip()

    greeting = f"Hi {sender_ref}," if sender_ref else "Hi,"

    if body_hint and body_hint.strip():
        body = f"{greeting}\n\n{body_hint.strip()}\n\nBest,"
    else:
        if tone == "friendly":
            body = f"{greeting}\n\nThanks for the update. I appreciate it.\n\nBest,"
        elif tone == "professional":
            body = f"{greeting}\n\nThank you for the update. I’ll review this and follow up shortly.\n\nBest regards,"
        else:
            body = f"{greeting}\n\nThanks for the note. I’ll follow up soon.\n\nBest,"

    return {"body": body, "tone": tone, "source": "template"}


def _revise_with_rules(current_body: str, instruction: str) -> str:
    body = (current_body or "").strip()
    instruction = (instruction or "").strip().lower()

    if not body:
        return ""

    compact_body = " ".join(line.strip() for line in body.splitlines() if line.strip())

    if "one line" in instruction or "one sentence" in instruction:
        sentences = re.split(r"(?<=[.!?])\s+", compact_body)
        if sentences and sentences[0].strip():
            return sentences[0].strip()
        return compact_body

    if "shorter" in instruction or "more concise" in instruction or "brief" in instruction or "too long" in instruction:
        sentences = re.split(r"(?<=[.!?])\s+", compact_body)
        if len(sentences) >= 1:
            return sentences[0].strip()
        if len(compact_body) > 100:
            return compact_body[:100].rstrip(" ,.;:") + "."
        return compact_body

    if "longer" in instruction or "more detail" in instruction:
        if compact_body.endswith("."):
            compact_body = compact_body[:-1]
        return (
            f"{compact_body}. "
            f"Please let me know if you have any questions or if you'd like me to send anything else."
        )

    if "mention friday" in instruction:
        if "friday" not in compact_body.lower():
            if compact_body.endswith("."):
                compact_body = compact_body[:-1]
            return f"{compact_body}, and I’d appreciate your feedback by Friday."
        return compact_body

    if "attached the file" in instruction or "attached it" in instruction or "mention attachment" in instruction:
        if "attach" not in compact_body.lower():
            if compact_body.endswith("."):
                compact_body = compact_body[:-1]
            return f"{compact_body}. I attached the file for reference."
        return compact_body

    if "less cheesy" in instruction or "more direct" in instruction or "make it direct" in instruction:
        sentences = re.split(r"(?<=[.!?])\s+", compact_body)
        if sentences:
            return " ".join(sentences[:2]).strip()
        return compact_body

    if "friendlier" in instruction or "warmer" in instruction or "softer" in instruction:
        body2 = compact_body.replace("would appreciate", "would really appreciate")
        body2 = body2.replace("Please let me know", "Let me know")
        return body2

    if "more professional" in instruction or "formal" in instruction:
        body2 = compact_body.replace("would love", "would appreciate")
        body2 = body2.replace("Let me know", "Please let me know")
        return body2

    if "not like that" in instruction:
        sentences = re.split(r"(?<=[.!?])\s+", compact_body)
        if sentences:
            return sentences[0].strip()
        return compact_body

    return compact_body