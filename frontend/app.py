# frontend/app.py

import re
import html
import requests
import streamlit as st
from datetime import datetime

API_BASE = "http://127.0.0.1:8000"


# -----------------------
# PAGE SETUP
# -----------------------

st.set_page_config(
    page_title="ExecAI",
    page_icon="🤖",
    layout="wide",
)


# -----------------------
# CUSTOM STYLES
# -----------------------

st.markdown(
    """
    <style>
        .main-title {
            font-size: 2.2rem;
            font-weight: 800;
            margin-bottom: 0.2rem;
            letter-spacing: -0.02em;
        }

        .subtitle {
            color: #6b7280;
            font-size: 1rem;
            margin-bottom: 1.2rem;
        }

        .section-title {
            font-size: 1.2rem;
            font-weight: 700;
            margin-top: 0.25rem;
            margin-bottom: 0.8rem;
        }

        .card {
            border: 1px solid #e5e7eb;
            border-radius: 16px;
            padding: 1rem 1rem 0.85rem 1rem;
            margin-bottom: 0.85rem;
            background: #ffffff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }

        .card-title {
            font-size: 1.05rem;
            font-weight: 700;
            margin-bottom: 0.35rem;
            color: #111827;
        }

        .muted {
            color: #6b7280;
            font-size: 0.92rem;
        }

        .pill {
            display: inline-block;
            padding: 0.22rem 0.65rem;
            border-radius: 999px;
            background: #eef2ff;
            color: #3730a3;
            font-size: 0.78rem;
            font-weight: 700;
            margin-bottom: 0.75rem;
        }

        .sidebar-note {
            color: #6b7280;
            font-size: 0.92rem;
        }

        .empty-state {
            border: 1px dashed #d1d5db;
            border-radius: 16px;
            padding: 1rem 1.1rem;
            background: #fafafa;
            color: #374151;
            margin-top: 0.5rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# -----------------------
# SESSION STATE
# -----------------------

if "messages" not in st.session_state:
    st.session_state.messages = []

if "debug_last" not in st.session_state:
    st.session_state.debug_last = {
        "intent_data": None,
        "decision": None,
        "result": None,
    }


# -----------------------
# HELPERS
# -----------------------

def format_datetime(value: str) -> str:
    if not value:
        return ""

    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(value)
        dt = dt.astimezone(ZoneInfo("America/New_York"))
        return dt.strftime("%a, %b %d · %I:%M %p %Z")
    except Exception:
        return value


def clean_email_body(body: str, max_chars: int = 2000) -> str:
    if not body:
        return ""

    cleaned = body
    cleaned = html.unescape(cleaned)

    cleaned = re.sub(r"<script.*?>.*?</script>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style.*?>.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<xml.*?>.*?</xml>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<o:.*?>.*?</o:.*?>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<v:.*?>.*?</v:.*?>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)

    cleaned = re.sub(
        r"\b(width|height|font|color|background|margin|padding|display|line-height|border|mso-[a-z-]+)[^;>{}]*(;|:)",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = cleaned.replace("=\r\n", "")
    cleaned = cleaned.replace("=\n", "")
    cleaned = cleaned.replace("=20", " ")
    cleaned = cleaned.replace("=3D", "=")

    cleaned = re.sub(r"[ ​⁠]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if len(cleaned) > 600:
        match = re.search(
            r"(We've been perfecting jeans since 1873.*|I hope.*|Hello.*|Hi.*|Thank you.*|Your order.*|Your account.*)",
            cleaned,
            flags=re.IGNORECASE,
        )
        if match:
            cleaned = match.group(1)

    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "..."

    return cleaned


def append_assistant_message(decision: dict, result):
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": "",
            "decision": decision,
            "result": result,
        }
    )


def submit_prompt(prompt: str):
    if not prompt.strip():
        return

    st.session_state.messages.append(
        {
            "role": "user",
            "content": prompt,
        }
    )

    try:
        res = requests.post(
            f"{API_BASE}/assistant",
            json={"text": prompt},
            timeout=25,
        )
        res.raise_for_status()
        data = res.json()

        intent_data = data.get("intent_data", {})
        decision = data.get("decision", {})
        result = data.get("result")

        st.session_state.debug_last = {
            "intent_data": intent_data,
            "decision": decision,
            "result": result,
        }

        intent = (intent_data.get("intent") or "").strip()
        entities = intent_data.get("entities") or {}

        if intent == "email_drafting" and result is None:
            try:
                draft_res = requests.post(
                    f"{API_BASE}/draft-email",
                    json={
                        "recipient": entities.get("recipient"),
                        "topic": entities.get("topic"),
                        "tone": entities.get("tone", "professional"),
                        "original_text": prompt,
                    },
                    timeout=10,
                )
                draft_res.raise_for_status()
                result = draft_res.json()
            except Exception as e:
                result = {
                    "status": "error",
                    "detail": f"Draft email error: {e}",
                }

        append_assistant_message(decision, result)

    except Exception as e:
        append_assistant_message(
            {"message": "Backend error"},
            {"status": "error", "detail": str(e)},
        )


def create_event_directly(title: str, start: str, duration_min: int, attendee_emails=None):
    attendee_emails = attendee_emails or []

    try:
        res = requests.post(
            f"{API_BASE}/integrations/google/create-event",
            json={
                "title": title,
                "start": start,
                "duration_min": int(duration_min),
                "attendees": attendee_emails,
                "description": "",
                "send_notifications": True,
            },
            timeout=25,
        )
        res.raise_for_status()
        result = res.json()

        decision = {
            "action": "create_event",
            "intent": "create_event",
            "provider": "google",
            "title": title,
            "start": start,
            "duration_min": int(duration_min),
            "attendee_emails": attendee_emails,
            "message": "Alternative event created.",
        }

        st.session_state.debug_last = {
            "intent_data": {
                "intent": "create_event",
                "entities": {
                    "title": title,
                    "start": start,
                    "duration_min": int(duration_min),
                    "attendee_emails": attendee_emails,
                },
                "mode": "ui_direct_action",
                "note": "Created directly from alternative time button.",
                "original_text": f"Create alternative event {title}",
            },
            "decision": decision,
            "result": result,
        }

        append_assistant_message(decision, result)
        st.rerun()

    except Exception as e:
        decision = {
            "action": "create_event",
            "intent": "create_event",
            "provider": "google",
            "title": title,
            "start": start,
            "duration_min": int(duration_min),
            "attendee_emails": attendee_emails,
            "message": "Failed to create alternative event.",
        }
        result = {
            "status": "error",
            "detail": str(e),
        }

        st.session_state.debug_last = {
            "intent_data": {
                "intent": "create_event",
                "entities": {
                    "title": title,
                    "start": start,
                    "duration_min": int(duration_min),
                    "attendee_emails": attendee_emails,
                },
                "mode": "ui_direct_action",
                "note": "Alternative time creation failed.",
                "original_text": f"Create alternative event {title}",
            },
            "decision": decision,
            "result": result,
        }

        append_assistant_message(decision, result)
        st.rerun()


# -----------------------
# RENDER HELPERS
# -----------------------

def render_event_list(result: dict):
    events = result.get("events", []) or []

    st.markdown('<div class="section-title">📅 Upcoming events</div>', unsafe_allow_html=True)

    if not events:
        st.info("No events found for that time range.")
        return

    for event in events:
        title = event.get("title") or "(No title)"
        start = format_datetime(event.get("start") or "")
        end = format_datetime(event.get("end") or "")
        link = event.get("htmlLink")

        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="pill">Google Calendar</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="card-title">{title}</div>', unsafe_allow_html=True)

        if start and end:
            st.markdown(f"**Time:** {start} → {end}")
        elif start:
            st.markdown(f"**Time:** {start}")

        if link:
            st.markdown(f"[Open in Google Calendar]({link})")

        st.markdown("</div>", unsafe_allow_html=True)


def render_created_event(result: dict):
    event = result.get("event", {}) or {}

    st.markdown('<div class="section-title">✅ Event created</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="pill">Google Calendar</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="card-title">{event.get("title", "(No title)")}</div>', unsafe_allow_html=True)

    if event.get("start"):
        st.markdown(f"**Start:** {format_datetime(event.get('start'))}")
    if event.get("end"):
        st.markdown(f"**End:** {format_datetime(event.get('end'))}")

    attendees = event.get("attendees", []) or []
    if attendees:
        st.markdown("**Attendees:**")
        for attendee in attendees:
            email = attendee.get("email", "")
            status = attendee.get("status", "needsAction")
            st.markdown(f"- {email} — `{status}`")

    if event.get("htmlLink"):
        st.markdown(f"[Open in Google Calendar]({event.get('htmlLink')})")

    st.markdown("</div>", unsafe_allow_html=True)


def render_email_list(result: dict):
    emails = result.get("emails", []) or []

    st.markdown('<div class="section-title">📧 Latest emails</div>', unsafe_allow_html=True)

    if not emails:
        st.info("No emails found.")
        return

    for email_data in emails:
        subject = email_data.get("subject") or "(No subject)"
        sender = email_data.get("from") or "(Unknown sender)"
        date_value = email_data.get("date") or ""
        snippet = email_data.get("snippet") or ""

        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="pill">Gmail</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="card-title">{subject}</div>', unsafe_allow_html=True)
        st.markdown(f"**From:** {sender}")

        if email_data.get("to"):
            st.markdown(f"**To:** {email_data.get('to')}")
        if date_value:
            st.markdown(f"**Date:** {date_value}")
        if snippet:
            st.markdown(f'<div class="muted">{snippet}</div>', unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)


def render_read_email(result: dict):
    email_data = result.get("email", {}) or {}

    subject = email_data.get("subject") or "(No subject)"
    sender = email_data.get("from") or "(Unknown sender)"
    to_val = email_data.get("to") or ""
    date_val = email_data.get("date") or ""
    snippet = email_data.get("snippet") or ""
    body = clean_email_body(email_data.get("body") or "")

    st.markdown('<div class="section-title">📩 Opened email</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="pill">Gmail Message</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="card-title">{subject}</div>', unsafe_allow_html=True)
    st.markdown(f"**From:** {sender}")

    if to_val:
        st.markdown(f"**To:** {to_val}")
    if date_val:
        st.markdown(f"**Date:** {date_val}")
    if snippet:
        st.markdown(f"**Snippet:** {snippet}")
    if email_data.get("threadId"):
        st.markdown(f"**Thread ID:** `{email_data.get('threadId')}`")

    st.text_area(
        "Email body",
        value=body,
        height=320,
        disabled=True,
        key=f"read_body_{email_data.get('id', 'x')}",
    )

    st.markdown("</div>", unsafe_allow_html=True)


def render_created_draft(result: dict):
    draft = result.get("draft", {}) or {}
    email_data = result.get("email", {}) or {}

    st.markdown('<div class="section-title">✉️ Gmail draft created</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="pill">Gmail Draft</div>', unsafe_allow_html=True)
    st.markdown(f"**To:** {email_data.get('to', '')}")
    st.markdown(f"**Subject:** {email_data.get('subject', '')}")

    st.text_area(
        "Draft body",
        value=email_data.get("body", ""),
        height=180,
        disabled=True,
        key=f"draft_body_{draft.get('id', 'x')}",
    )

    if draft.get("id"):
        st.markdown(f"**Draft ID:** `{draft.get('id')}`")
    if draft.get("threadId"):
        st.markdown(f"**Thread ID:** `{draft.get('threadId')}`")

    st.success("The draft was created successfully in Gmail.")
    st.markdown("</div>", unsafe_allow_html=True)


def render_reply_draft(result: dict):
    draft = result.get("draft", {}) or {}
    email_data = result.get("email", {}) or {}

    st.markdown('<div class="section-title">↩️ Reply draft created</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="pill">Gmail Reply Draft</div>', unsafe_allow_html=True)
    st.markdown(f"**To:** {email_data.get('to', '')}")
    st.markdown(f"**Subject:** {email_data.get('subject', '')}")

    st.text_area(
        "Reply body",
        value=email_data.get("body", ""),
        height=180,
        disabled=True,
        key=f"reply_body_{draft.get('id', 'x')}",
    )

    if draft.get("id"):
        st.markdown(f"**Draft ID:** `{draft.get('id')}`")
    if draft.get("threadId"):
        st.markdown(f"**Thread ID:** `{draft.get('threadId')}`")

    st.success("The reply draft was created successfully in Gmail.")
    st.markdown("</div>", unsafe_allow_html=True)


def render_mock_draft(result: dict):
    email_data = result.get("email", {}) or {}

    st.markdown('<div class="section-title">✉️ Draft email</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="pill">Mock Draft</div>', unsafe_allow_html=True)
    st.markdown(f"**To:** {email_data.get('to', '')}")
    st.markdown(f"**Subject:** {email_data.get('subject', '')}")
    st.text_area("Body", value=email_data.get("body", ""), height=220, disabled=True, key="mock_draft_body")
    st.markdown("</div>", unsafe_allow_html=True)


def render_meeting_options(decision: dict):
    options = decision.get("options", []) or []
    if not options:
        st.info(decision.get("message", "No meeting options found."))
        return

    st.markdown('<div class="section-title">🗓 Suggested times</div>', unsafe_allow_html=True)

    busy_display = decision.get("busy_display", []) or []
    if busy_display:
        st.warning("Busy times: " + " · ".join(busy_display))

    for opt in options:
        label = opt.get("label", "Option")
        start = format_datetime(opt.get("start", ""))
        dur = opt.get("duration_min", 30)

        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="pill">Scheduling</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="card-title">{label}</div>', unsafe_allow_html=True)
        st.markdown(f"**Start:** {start}")
        st.markdown(f"**Duration:** {dur} minutes")
        st.markdown("</div>", unsafe_allow_html=True)


def render_conflicts_from_list(
    conflicts: list,
    title_text: str = "⚠️ Conflicts found",
    warning_text: str = "Conflict detected.",
):
    st.markdown(f'<div class="section-title">{title_text}</div>', unsafe_allow_html=True)
    st.warning(warning_text)

    for c in conflicts:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="pill">Conflict</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="card-title">{c.get("title", "Busy")}</div>', unsafe_allow_html=True)
        st.markdown(f"**From:** {c.get('start', '?')}")
        st.markdown(f"**To:** {c.get('end', '?')}")
        st.markdown("</div>", unsafe_allow_html=True)


def render_alternatives(alternatives: list, proposed_event: dict | None = None, card_key_prefix: str = "alt"):
    if not alternatives:
        return

    proposed_event = proposed_event or {}
    event_title = proposed_event.get("title") or "Meeting"
    attendee_emails = proposed_event.get("attendee_emails") or []

    st.markdown('<div class="section-title">🕒 Alternative times</div>', unsafe_allow_html=True)

    for idx, alt in enumerate(alternatives):
        label = alt.get("label", f"Option {idx + 1}")
        start_raw = alt.get("start", "")
        start = format_datetime(start_raw)
        dur = alt.get("duration_min", proposed_event.get("duration_min", 30))

        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="pill">Alternative</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="card-title">{label}</div>', unsafe_allow_html=True)
        st.markdown(f"**Start:** {start}")
        st.markdown(f"**Duration:** {dur} minutes")

        if attendee_emails:
            st.markdown("**Attendees:** " + ", ".join(attendee_emails))

        button_key = f"{card_key_prefix}_{idx}_{start_raw}_{event_title}"
        if st.button(f"Create this event ({label})", key=button_key, use_container_width=True):
            create_event_directly(
                title=event_title,
                start=start_raw,
                duration_min=int(dur),
                attendee_emails=attendee_emails,
            )

        st.markdown("</div>", unsafe_allow_html=True)


def render_conflicts(decision: dict):
    render_conflicts_from_list(
        decision.get("conflicts", []) or [],
        "⚠️ Conflicts found",
        decision.get("message", "Conflict detected."),
    )


def render_proposed_event(proposed: dict):
    if not proposed:
        return

    st.markdown('<div class="section-title">📌 Proposed event</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="pill">Not created</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="card-title">{proposed.get("title", "(No title)")}</div>', unsafe_allow_html=True)

    if proposed.get("start"):
        st.markdown(f"**Start:** {format_datetime(proposed.get('start'))}")
    if proposed.get("duration_min"):
        st.markdown(f"**Duration:** {proposed.get('duration_min')} minutes")
    if proposed.get("attendee_emails"):
        st.markdown("**Attendees:** " + ", ".join(proposed.get("attendee_emails")))

    st.markdown("</div>", unsafe_allow_html=True)


def render_needs_clarification(result: dict):
    st.markdown('<div class="section-title">❓ More info needed</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.warning(result.get("message", "I need a bit more information."))

    missing = result.get("missing", []) or []
    if missing:
        st.markdown("**Missing:** " + ", ".join(missing))

    example = result.get("example")
    if example:
        st.markdown(f"**Example:** `{example}`")

    st.markdown("</div>", unsafe_allow_html=True)


def render_generic_message(decision: dict):
    message = decision.get("message") or "Done."
    st.info(message)


def render_reply_and_create_event(result: dict):
    status = result.get("status")

    if status == "success":
        st.success(result.get("message", "Reply draft and calendar event created successfully."))
        reply_result = result.get("reply", {}) or {}
        calendar_result = result.get("calendar", {}) or {}

        if reply_result:
            render_reply_draft(reply_result)
        if calendar_result and calendar_result.get("status") == "created":
            render_created_event(calendar_result)

        return

    if status == "partial_success":
        st.warning(result.get("message", "Partial success."))
        reply_result = result.get("reply", {}) or {}
        calendar_result = result.get("calendar", {}) or {}

        if reply_result:
            render_reply_draft(reply_result)

        cal_status = calendar_result.get("status")
        if cal_status == "conflict_detected":
            render_conflicts_from_list(
                calendar_result.get("conflicts", []) or [],
                "⚠️ Calendar conflict detected",
                calendar_result.get("message", "Event not created because of a conflict."),
            )
            render_proposed_event(calendar_result.get("proposed_event", {}) or {})
            render_alternatives(
                calendar_result.get("alternatives", []) or [],
                calendar_result.get("proposed_event", {}) or {},
                card_key_prefix="reply_create_alt",
            )
        elif cal_status == "needs_clarification":
            render_needs_clarification(calendar_result)
        elif cal_status == "created":
            render_created_event(calendar_result)

        return

    if status == "not_found":
        st.warning(result.get("message", "Nothing found."))
        return

    if status == "needs_clarification":
        render_needs_clarification(result)
        return

    render_generic_message({"message": result.get("message", "Done.")})


def render_assistant_result(decision: dict, result):
    action = (decision or {}).get("action") or ""

    if isinstance(result, dict) and result.get("status") == "error":
        st.error(f"Integration error: {result.get('detail', 'Unknown error')}")
        return

    if isinstance(result, dict) and result.get("status") == "needs_clarification":
        render_needs_clarification(result)
        return

    if isinstance(result, dict) and result.get("status") == "not_found":
        st.warning(result.get("message", "Nothing found."))
        return

    if action == "list_events" and isinstance(result, dict):
        render_event_list(result)
        return

    if action == "create_event":
        if isinstance(result, dict) and result.get("status") == "conflict_detected":
            render_conflicts_from_list(
                result.get("conflicts", []) or [],
                "⚠️ Calendar conflict detected",
                result.get("message", "Conflict detected."),
            )
            proposed = result.get("proposed_event", {}) or {}
            render_proposed_event(proposed)
            render_alternatives(
                result.get("alternatives", []) or [],
                proposed,
                card_key_prefix="create_event_alt",
            )
            return

        if decision.get("has_conflicts"):
            render_conflicts(decision)
            proposed = {
                "title": decision.get("title"),
                "start": decision.get("start"),
                "duration_min": decision.get("duration_min"),
                "attendee_emails": decision.get("attendee_emails", []),
            }
            render_proposed_event(proposed)
            render_alternatives(
                decision.get("alternatives", []) or [],
                proposed,
                card_key_prefix="decision_alt",
            )
            return

        if isinstance(result, dict) and result.get("status") == "created":
            render_created_event(result)
            return

        render_generic_message(decision)
        return

    if action == "list_emails" and isinstance(result, dict):
        render_email_list(result)
        return

    if action == "read_email" and isinstance(result, dict) and "email" in result:
        render_read_email(result)
        return

    if action in {"create_draft", "draft_email"} and isinstance(result, dict):
        if result.get("status") == "draft_created":
            render_created_draft(result)
            return
        if result.get("status") == "drafted":
            render_mock_draft(result)
            return

    if action == "reply_email" and isinstance(result, dict):
        if result.get("status") == "reply_draft_created":
            render_reply_draft(result)
            return

    if action == "reply_and_create_event" and isinstance(result, dict):
        render_reply_and_create_event(result)
        return

    if action == "suggest_times":
        render_meeting_options(decision)
        return

    render_generic_message(decision)


# -----------------------
# SIDEBAR
# -----------------------

with st.sidebar:
    st.markdown("## ExecAI")
    st.markdown(
        '<div class="sidebar-note">Your AI executive assistant for scheduling and email workflows.</div>',
        unsafe_allow_html=True,
    )
    st.divider()

    st.markdown("### Quick actions")

    if st.button("📧 Show latest emails", use_container_width=True):
        submit_prompt("show my latest emails")
        st.rerun()

    if st.button("📩 Read latest email", use_container_width=True):
        submit_prompt("read my latest email")
        st.rerun()

    if st.button("↩️ Reply to latest email", use_container_width=True):
        submit_prompt('reply to my latest email saying "Thanks for the update"')
        st.rerun()

    if st.button("🤝 Reply + create meeting", use_container_width=True):
        submit_prompt('reply to my latest email saying "I am available tomorrow at 2pm" and create the meeting')
        st.rerun()

    if st.button("📅 Show my calendar for next week", use_container_width=True):
        submit_prompt("show my calendar for next week")
        st.rerun()

    if st.button("🗓 Find a time to meet tomorrow", use_container_width=True):
        submit_prompt("find a time to meet tomorrow")
        st.rerun()

    if st.button("➕ Create a demo event", use_container_width=True):
        submit_prompt("create an event called Demo Sync tomorrow at 2pm for 30 minutes")
        st.rerun()

    if st.button("✉️ Create demo draft", use_container_width=True):
        submit_prompt("draft an email to sarah@example.com about the proposal")
        st.rerun()

    st.divider()

    if st.button("🧹 Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.debug_last = {
            "intent_data": None,
            "decision": None,
            "result": None,
        }
        st.rerun()

    show_debug = st.toggle("Show debug", value=False)


# -----------------------
# HEADER
# -----------------------

st.markdown('<div class="main-title">ExecAI</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">A lightweight executive assistant MVP with real Google Calendar and Gmail integrations.</div>',
    unsafe_allow_html=True,
)


# -----------------------
# CHAT HISTORY
# -----------------------

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["content"])
        else:
            decision = msg.get("decision") or {}
            result = msg.get("result")
            render_assistant_result(decision, result)


# -----------------------
# EMPTY STATE
# -----------------------

if not st.session_state.messages:
    st.markdown(
        """
        <div class="empty-state">
            <strong>Try asking something like:</strong><br><br>
            • show my latest emails<br>
            • read my latest email<br>
            • reply to my latest email saying "Thanks for the update"<br>
            • reply to my latest email saying "I am available tomorrow at 2pm" and create the meeting<br>
            • create a meeting with sarah@example.com tomorrow at 11am<br>
            • schedule a budget review with sarah@example.com and john@example.com tomorrow at 11am for 45 minutes<br>
            • show my calendar for next week<br>
            • create an event called Budget Review tomorrow at 2pm for 45 minutes<br>
            • draft an email to sarah@example.com about the proposal<br>
            • find a time to meet tomorrow
        </div>
        """,
        unsafe_allow_html=True,
    )


# -----------------------
# CHAT INPUT
# -----------------------

prompt = st.chat_input("Ask ExecAI something...")

if prompt:
    st.session_state.messages.append(
        {
            "role": "user",
            "content": prompt,
        }
    )

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("ExecAI is thinking..."):
            try:
                res = requests.post(
                    f"{API_BASE}/assistant",
                    json={"text": prompt},
                    timeout=25,
                )
                res.raise_for_status()
                data = res.json()

                intent_data = data.get("intent_data", {})
                decision = data.get("decision", {})
                result = data.get("result")

                st.session_state.debug_last = {
                    "intent_data": intent_data,
                    "decision": decision,
                    "result": result,
                }

                intent = (intent_data.get("intent") or "").strip()
                entities = intent_data.get("entities") or {}

                if intent == "email_drafting" and result is None:
                    try:
                        draft_res = requests.post(
                            f"{API_BASE}/draft-email",
                            json={
                                "recipient": entities.get("recipient"),
                                "topic": entities.get("topic"),
                                "tone": entities.get("tone", "professional"),
                                "original_text": prompt,
                            },
                            timeout=10,
                        )
                        draft_res.raise_for_status()
                        result = draft_res.json()
                    except Exception as e:
                        result = {
                            "status": "error",
                            "detail": f"Draft email error: {e}",
                        }

                render_assistant_result(decision, result)
                append_assistant_message(decision, result)

            except Exception as e:
                st.error(f"Backend error: {e}")
                append_assistant_message(
                    {"message": "Backend error"},
                    {"status": "error", "detail": str(e)},
                )


# -----------------------
# DEBUG PANEL
# -----------------------

if show_debug:
    with st.expander("Debug (Intent + Decision + Result)", expanded=False):
        dbg = st.session_state.debug_last or {}

        if dbg.get("intent_data") is not None:
            st.markdown("### Detected intent")
            st.json(dbg["intent_data"])

        if dbg.get("decision") is not None:
            st.markdown("### Decision / Orchestration")
            st.json(dbg["decision"])

        if dbg.get("result") is not None:
            st.markdown("### Result")
            st.json(dbg["result"])