import re
import html
import requests
import streamlit as st
from datetime import datetime, timedelta

API_BASE = "http://localhost:8000"


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

if "google_auth_url" not in st.session_state:
    st.session_state.google_auth_url = None

if "outlook_auth_url" not in st.session_state:
    st.session_state.outlook_auth_url = None

if "google_status" not in st.session_state:
    st.session_state.google_status = False

if "outlook_status" not in st.session_state:
    st.session_state.outlook_status = False

if "calendar_provider" not in st.session_state:
    st.session_state.calendar_provider = "google"


# -----------------------
# HELPERS
# -----------------------

def provider_label(provider: str) -> str:
    provider = (provider or "").strip().lower()
    if provider == "outlook":
        return "Outlook"
    if provider == "google":
        return "Google Calendar"
    return "Calendar"


def provider_open_label(provider: str) -> str:
    provider = (provider or "").strip().lower()
    if provider == "outlook":
        return "Open in Outlook"
    if provider == "google":
        return "Open in Google Calendar"
    return "Open in Calendar"


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

    cleaned = re.sub(r"[ \u200b\u2060]+", " ", cleaned)
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


def check_connection_status():
    try:
        res = requests.get(f"{API_BASE}/integrations/status", timeout=10)
        res.raise_for_status()
        data = res.json()
        st.session_state.google_status = bool(data.get("google_connected", False))
        st.session_state.outlook_status = bool(data.get("outlook_connected", False))
    except Exception:
        st.session_state.google_status = False
        st.session_state.outlook_status = False


def prepare_connect(provider: str):
    provider = (provider or "").strip().lower()
    try:
        res = requests.get(f"{API_BASE}/integrations/{provider}/auth-url", timeout=15, allow_redirects=False)
        res.raise_for_status()

        auth_url = None

        if 300 <= res.status_code < 400:
            auth_url = res.headers.get("Location")
        else:
            data = res.json()
            auth_url = data.get("auth_url")

        if provider == "google":
            st.session_state.google_auth_url = auth_url
        elif provider == "outlook":
            st.session_state.outlook_auth_url = auth_url

        if not auth_url:
            st.error(f"Could not generate {provider.title()} authorization link.")
    except Exception as e:
        if provider == "google":
            st.session_state.google_auth_url = None
        elif provider == "outlook":
            st.session_state.outlook_auth_url = None
        st.error(f"{provider.title()} connect error: {e}")


def _assistant_payload(prompt: str) -> dict:
    """Build the assistant request payload, always including the selected provider."""
    return {
        "text": prompt,
        "provider": st.session_state.calendar_provider,
    }


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
            json=_assistant_payload(prompt),
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
    provider = st.session_state.calendar_provider

    try:
        res = requests.post(
            f"{API_BASE}/integrations/{provider}/create-event",
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
            "provider": provider,
            "title": title,
            "start": start,
            "duration_min": int(duration_min),
            "attendee_emails": attendee_emails,
            "message": "Event created from suggested time.",
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
                "note": "Created directly from suggested/alternative time button.",
                "original_text": f"Create event {title}",
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
            "provider": provider,
            "title": title,
            "start": start,
            "duration_min": int(duration_min),
            "attendee_emails": attendee_emails,
            "message": "Failed to create event from suggested time.",
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
                "note": "Suggested time creation failed.",
                "original_text": f"Create event {title}",
            },
            "decision": decision,
            "result": result,
        }

        append_assistant_message(decision, result)
        st.rerun()


def demo_show_upcoming_meetings(provider: str):
    try:
        res = requests.post(
            f"{API_BASE}/integrations/{provider}/list-events",
            json={"days": 7},
            timeout=20,
        )

        if res.status_code >= 400:
            try:
                return {
                    "status": "error",
                    "detail": res.json().get("detail", res.text),
                }
            except Exception:
                return {
                    "status": "error",
                    "detail": res.text,
                }

        return res.json()

    except Exception as e:
        return {"status": "error", "detail": str(e)}


def demo_check_free_time_tomorrow(provider: str):
    try:
        tomorrow = datetime.now() + timedelta(days=1)
        start_dt = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0).astimezone()
        end_dt = tomorrow.replace(hour=17, minute=0, second=0, microsecond=0).astimezone()

        payload = {
            "time_min": start_dt.isoformat(),
            "time_max": end_dt.isoformat(),
            "calendar_ids": ["primary"] if provider == "google" else [],
        }

        res = requests.post(
            f"{API_BASE}/integrations/{provider}/freebusy",
            json=payload,
            timeout=20,
        )

        if res.status_code >= 400:
            try:
                return {
                    "status": "error",
                    "detail": res.json().get("detail", res.text),
                }
            except Exception:
                return {
                    "status": "error",
                    "detail": res.text,
                }

        return res.json()

    except Exception as e:
        return {"status": "error", "detail": str(e)}


def demo_create_draft(to: str, subject: str, body: str):
    provider = st.session_state.calendar_provider
    try:
        res = requests.post(
            f"{API_BASE}/integrations/google/create-draft" if provider == "google"
            else f"{API_BASE}/assistant",
            json=(
                {"to": to, "subject": subject, "body": body}
                if provider == "google"
                else _assistant_payload(f"draft an email to {to} with subject {subject}: {body}")
            ),
            timeout=20,
        )
        res.raise_for_status()
        return res.json()
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# -----------------------
# RENDER HELPERS
# -----------------------

def render_event_list(result: dict):
    events = result.get("events", []) or []
    provider = result.get("provider", "google")

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
        st.markdown(f'<div class="pill">{provider_label(provider)}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="card-title">{title}</div>', unsafe_allow_html=True)

        if start and end:
            st.markdown(f"**Time:** {start} → {end}")
        elif start:
            st.markdown(f"**Time:** {start}")

        if link:
            st.markdown(f"[{provider_open_label(provider)}]({link})")

        st.markdown("</div>", unsafe_allow_html=True)


def render_created_event(result: dict):
    event = result.get("event", {}) or {}
    provider = result.get("provider", st.session_state.calendar_provider)

    st.markdown('<div class="section-title">✅ Event created</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f'<div class="pill">{provider_label(provider)}</div>', unsafe_allow_html=True)
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
        st.markdown(f"[{provider_open_label(provider)}]({event.get('htmlLink')})")

    st.markdown("</div>", unsafe_allow_html=True)


def render_email_list(result: dict):
    emails = result.get("emails", []) or []
    provider = result.get("provider", "google")
    pill_label = "Outlook Mail" if provider == "outlook" else "Gmail"

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
        st.markdown(f'<div class="pill">{pill_label}</div>', unsafe_allow_html=True)
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
    provider = result.get("provider", "google")
    pill_label = "Outlook Message" if provider == "outlook" else "Gmail Message"

    subject = email_data.get("subject") or "(No subject)"
    sender = email_data.get("from") or "(Unknown sender)"
    to_val = email_data.get("to") or ""
    date_val = email_data.get("date") or ""
    snippet = email_data.get("snippet") or ""
    body = clean_email_body(email_data.get("body") or "")

    st.markdown('<div class="section-title">📩 Opened email</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f'<div class="pill">{pill_label}</div>', unsafe_allow_html=True)
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
    provider = st.session_state.calendar_provider
    pill_label = "Outlook Draft" if provider == "outlook" else "Gmail Draft"

    draft_id = draft.get("id", "x")
    sent_key = f"sent_draft_{draft_id}"

    st.markdown('<div class="section-title">✉️ Draft created</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f'<div class="pill">{pill_label}</div>', unsafe_allow_html=True)
    st.markdown(f"**To:** {email_data.get('to', '')}")
    st.markdown(f"**Subject:** {email_data.get('subject', '')}")

    if st.session_state.get(sent_key):
        st.text_area(
            "Draft body",
            value=email_data.get("body", ""),
            height=180,
            disabled=True,
            key=f"draft_body_{draft_id}",
        )
        st.success("Email sent successfully!")
    else:
        edited_body = st.text_area(
            "Draft body (edit before sending)",
            value=email_data.get("body", ""),
            height=180,
            key=f"draft_body_{draft_id}",
        )

        if st.button("📤 Send Email", key=f"send_draft_{draft_id}"):
            try:
                send_res = requests.post(
                    f"{API_BASE}/integrations/{provider}/send-email",
                    json={
                        "to": email_data.get("to", ""),
                        "subject": email_data.get("subject", ""),
                        "body": edited_body,
                        "thread_id": draft.get("threadId"),
                    },
                    timeout=15,
                )
                send_res.raise_for_status()
                st.session_state[sent_key] = True
                st.rerun()
            except Exception as e:
                st.error(f"Failed to send: {e}")

    st.markdown("</div>", unsafe_allow_html=True)


def render_reply_draft(result: dict):
    draft = result.get("draft", {}) or {}
    email_data = result.get("email", {}) or {}
    provider = st.session_state.calendar_provider
    pill_label = "Outlook Reply Draft" if provider == "outlook" else "Gmail Reply Draft"

    draft_id = draft.get("id", "x")
    sent_key = f"sent_reply_{draft_id}"

    st.markdown('<div class="section-title">↩️ Reply draft created</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f'<div class="pill">{pill_label}</div>', unsafe_allow_html=True)
    st.markdown(f"**To:** {email_data.get('to', '')}")
    st.markdown(f"**Subject:** {email_data.get('subject', '')}")

    if st.session_state.get(sent_key):
        st.text_area(
            "Reply body",
            value=email_data.get("body", ""),
            height=180,
            disabled=True,
            key=f"reply_body_{draft_id}",
        )
        st.success("Reply sent successfully!")
    else:
        edited_body = st.text_area(
            "Reply body (edit before sending)",
            value=email_data.get("body", ""),
            height=180,
            key=f"reply_body_{draft_id}",
        )

        if st.button("📤 Send Reply", key=f"send_reply_{draft_id}"):
            try:
                send_res = requests.post(
                    f"{API_BASE}/integrations/{provider}/send-email",
                    json={
                        "to": email_data.get("to", ""),
                        "subject": email_data.get("subject", ""),
                        "body": edited_body,
                        "thread_id": draft.get("threadId"),
                    },
                    timeout=15,
                )
                send_res.raise_for_status()
                st.session_state[sent_key] = True
                st.rerun()
            except Exception as e:
                st.error(f"Failed to send: {e}")

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


def render_meeting_options(decision: dict, result: dict, key_prefix: str = "suggest_times"):
    options = result.get("options", []) or decision.get("options", []) or []
    if not options:
        st.info(result.get("message") or decision.get("message") or "No meeting options found.")
        return

    title = result.get("title") or decision.get("title") or "Meeting"
    if title == "ExecAI Event":
        title = "Meeting"

    attendee_emails = result.get("attendee_emails", []) or decision.get("attendee_emails", []) or []
    busy_display = result.get("busy_display", []) or decision.get("busy_display", []) or []

    st.markdown('<div class="section-title">🗓 Suggested times</div>', unsafe_allow_html=True)

    if title:
        st.markdown(f"**Meeting title:** {title}")

    if attendee_emails:
        st.markdown("**Attendees:** " + ", ".join(attendee_emails))

    if busy_display:
        st.warning("Busy times: " + " · ".join(busy_display))

    for idx, opt in enumerate(options):
        label = opt.get("label", f"Option {idx + 1}")
        start_raw = opt.get("start", "")
        start = format_datetime(start_raw)
        dur = opt.get("duration_min", result.get("duration_min", 30))

        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown(f'<div class="pill">{provider_label(st.session_state.calendar_provider)}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="card-title">{label}</div>', unsafe_allow_html=True)
        st.markdown(f"**Start:** {start}")
        st.markdown(f"**Duration:** {dur} minutes")

        if attendee_emails:
            st.markdown("**Attendees:** " + ", ".join(attendee_emails))

        button_key = f"{key_prefix}_{idx}_{start_raw}_{title}_{dur}"
        if st.button(f"Create this meeting ({label})", key=button_key, use_container_width=True):
            create_event_directly(
                title=title,
                start=start_raw,
                duration_min=int(dur),
                attendee_emails=attendee_emails,
            )

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
        st.markdown(f'<div class="pill">{provider_label(st.session_state.calendar_provider)}</div>', unsafe_allow_html=True)
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
    st.markdown(f'<div class="pill">{provider_label(st.session_state.calendar_provider)}</div>', unsafe_allow_html=True)
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


def render_draft_and_create_event(result: dict):
    status = result.get("status")

    if status == "success":
        st.success(result.get("message", "Draft email and calendar event created successfully."))
        draft_result = result.get("draft", {}) or {}
        calendar_result = result.get("calendar", {}) or {}

        if draft_result:
            render_created_draft(draft_result)
        if calendar_result and calendar_result.get("status") == "created":
            render_created_event(calendar_result)
        return

    if status == "partial_success":
        st.warning(result.get("message", "Partial success."))
        draft_result = result.get("draft", {}) or {}
        calendar_result = result.get("calendar", {}) or {}

        if draft_result:
            render_created_draft(draft_result)

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
                card_key_prefix="draft_create_alt",
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

    if action == "draft_email_and_create_event" and isinstance(result, dict):
        render_draft_and_create_event(result)
        return

    if action == "suggest_times" and isinstance(result, dict):
        unique_prefix = f"suggest_times_{abs(hash(str(decision) + str(result)))}"
        render_meeting_options(decision, result, key_prefix=unique_prefix)
        return

    render_generic_message(decision)


# -----------------------
# SIDEBAR
# -----------------------

check_connection_status()

with st.sidebar:
    st.markdown("## ExecAI")
    st.markdown(
        '<div class="sidebar-note">Your AI executive assistant for scheduling and email workflows.</div>',
        unsafe_allow_html=True,
    )
    st.divider()

    st.markdown("### Provider")
    st.session_state.calendar_provider = st.selectbox(
        "Choose provider",
        ["google", "outlook"],
        index=0 if st.session_state.calendar_provider == "google" else 1,
        format_func=lambda x: "Google" if x == "google" else "Outlook",
    )

    st.divider()

    st.markdown("### Google connection")
    if st.session_state.google_status:
        st.success("Google is connected.")
    else:
        st.warning("Google is not connected.")

    if st.button("🔗 Connect Google", use_container_width=True):
        prepare_connect("google")
        st.rerun()

    if st.session_state.google_auth_url:
        st.markdown(f"[Authorize Google account]({st.session_state.google_auth_url})")

    st.divider()

    st.markdown("### Outlook connection")
    if st.session_state.outlook_status:
        st.success("Outlook is connected.")
    else:
        st.warning("Outlook is not connected.")

    if st.button("🔗 Connect Outlook", use_container_width=True):
        prepare_connect("outlook")
        st.rerun()

    if st.session_state.outlook_auth_url:
        st.markdown(f"[Authorize Outlook account]({st.session_state.outlook_auth_url})")

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

    if st.button("✉️ Draft + create meeting", use_container_width=True):
        submit_prompt('draft an email to sarah@example.com saying "I am available tomorrow at 2pm" and create the meeting')
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
        st.session_state.google_auth_url = None
        st.session_state.outlook_auth_url = None
        st.rerun()

    show_debug = st.toggle("Show debug", value=False)


# -----------------------
# HEADER
# -----------------------

st.markdown('<div class="main-title">ExecAI</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">A lightweight executive assistant MVP with Google Calendar, Outlook Calendar, and email integrations.</div>',
    unsafe_allow_html=True,
)


# -----------------------
# DEMO PANELS
# -----------------------

selected_provider = st.session_state.calendar_provider
demo_col1, demo_col2, demo_col3 = st.columns(3)

with demo_col1:
    st.markdown(f"### Upcoming Meetings ({selected_provider.title()})")
    if st.button("Show my next meetings", use_container_width=True):
        demo_events = demo_show_upcoming_meetings(selected_provider)
        if demo_events.get("status") == "error":
            st.error(f"Could not load meetings: {demo_events.get('detail', 'Unknown error')}")
        else:
            events = demo_events.get("events", []) or []
            if not events:
                st.info("No meetings found.")
            else:
                for event in events[:5]:
                    st.markdown('<div class="card">', unsafe_allow_html=True)
                    st.markdown(f'<div class="pill">{provider_label(selected_provider)}</div>', unsafe_allow_html=True)
                    st.markdown(
                        f'<div class="card-title">{event.get("title") or "(No title)"}</div>',
                        unsafe_allow_html=True,
                    )
                    if event.get("start"):
                        st.markdown(f"**Start:** {format_datetime(event.get('start'))}")
                    if event.get("end"):
                        st.markdown(f"**End:** {format_datetime(event.get('end'))}")
                    if event.get("htmlLink"):
                        st.markdown(f"[{provider_open_label(selected_provider)}]({event.get('htmlLink')})")
                    st.markdown("</div>", unsafe_allow_html=True)

with demo_col2:
    st.markdown(f"### Free Time Tomorrow ({selected_provider.title()})")
    if st.button("Check availability tomorrow", use_container_width=True):
        freebusy = demo_check_free_time_tomorrow(selected_provider)
        if freebusy.get("status") == "error":
            st.error(f"Could not check availability: {freebusy.get('detail', 'Unknown error')}")
        else:
            busy_blocks = freebusy.get("busy_blocks", []) or []
            if not busy_blocks:
                st.success("You are free tomorrow between 9:00 AM and 5:00 PM.")
            else:
                st.warning("Busy times found tomorrow:")
                for block in busy_blocks:
                    st.markdown('<div class="card">', unsafe_allow_html=True)
                    st.markdown('<div class="pill">Busy</div>', unsafe_allow_html=True)
                    st.markdown(f"**From:** {format_datetime(block.get('start', ''))}")
                    st.markdown(f"**To:** {format_datetime(block.get('end', ''))}")
                    st.markdown("</div>", unsafe_allow_html=True)

with demo_col3:
    st.markdown(f"### Draft Email ({selected_provider.title()})")
    demo_to = st.text_input("Recipient", key="demo_to")
    demo_subject = st.text_input("Subject", key="demo_subject")
    demo_body = st.text_area("Message", key="demo_body", height=130)

    if st.button("Create Draft", use_container_width=True):
        draft_result = demo_create_draft(demo_to, demo_subject, demo_body)
        if draft_result.get("status") == "draft_created":
            st.success("Draft created.")
            render_created_draft(draft_result)
        else:
            st.error(f"Could not create draft: {draft_result.get('detail', 'Unknown error')}")

st.divider()


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
            • draft an email to sarah@example.com saying "I am available tomorrow at 2pm" and create the meeting<br>
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
                    json=_assistant_payload(prompt),
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