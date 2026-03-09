# frontend/app.py
import streamlit as st
import requests
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
        return datetime.fromisoformat(value).strftime("%a, %b %d · %I:%M %p")
    except Exception:
        return value


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
            timeout=20,
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

        # fallback only for old mock draft endpoint if needed
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

        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": "",
                "decision": decision,
                "result": result,
            }
        )

    except Exception as e:
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": "",
                "decision": {"message": "Backend error"},
                "result": {"status": "error", "detail": str(e)},
            }
        )


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

    for email in emails:
        subject = email.get("subject") or "(No subject)"
        sender = email.get("from") or "(Unknown sender)"
        date_value = email.get("date") or ""
        snippet = email.get("snippet") or ""

        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="pill">Gmail</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="card-title">{subject}</div>', unsafe_allow_html=True)
        st.markdown(f"**From:** {sender}")
        if date_value:
            st.markdown(f"**Date:** {date_value}")
        if snippet:
            st.markdown(f'<div class="muted">{snippet}</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)


def render_created_draft(result: dict):
    draft = result.get("draft", {}) or {}
    email = result.get("email", {}) or {}

    st.markdown('<div class="section-title">✉️ Gmail draft created</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="pill">Gmail Draft</div>', unsafe_allow_html=True)
    st.markdown(f"**To:** {email.get('to', '')}")
    st.markdown(f"**Subject:** {email.get('subject', '')}")
    st.text_area("Draft body", value=email.get("body", ""), height=180, disabled=True)

    if draft.get("id"):
        st.markdown(f"**Draft ID:** `{draft.get('id')}`")
    if draft.get("threadId"):
        st.markdown(f"**Thread ID:** `{draft.get('threadId')}`")

    st.success("The draft was created successfully in Gmail.")
    st.markdown("</div>", unsafe_allow_html=True)


def render_mock_draft(result: dict):
    email = result.get("email", {}) or {}

    st.markdown('<div class="section-title">✉️ Draft email</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="pill">Mock Draft</div>', unsafe_allow_html=True)
    st.markdown(f"**To:** {email.get('to', '')}")
    st.markdown(f"**Subject:** {email.get('subject', '')}")
    st.text_area("Body", value=email.get("body", ""), height=220, disabled=True)
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


def render_conflicts(decision: dict):
    st.markdown('<div class="section-title">⚠️ Conflicts found</div>', unsafe_allow_html=True)
    st.warning(decision.get("message", "Conflict detected."))

    for c in decision.get("conflicts", []):
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="pill">Conflict</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="card-title">{c.get("title", "Busy")}</div>', unsafe_allow_html=True)
        st.markdown(f"**From:** {c.get('start', '?')}")
        st.markdown(f"**To:** {c.get('end', '?')}")
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


def render_assistant_result(decision: dict, result: dict | None):
    action = (decision or {}).get("action") or ""

    if isinstance(result, dict) and result.get("status") == "error":
        st.error(f"Integration error: {result.get('detail', 'Unknown error')}")
        return

    if isinstance(result, dict) and result.get("status") == "needs_clarification":
        render_needs_clarification(result)
        return

    if action == "list_events" and isinstance(result, dict):
        render_event_list(result)
        return

    if action == "create_event":
        if decision.get("has_conflicts"):
            render_conflicts(decision)
            return
        if isinstance(result, dict) and result.get("status") == "created":
            render_created_event(result)
            return
        render_generic_message(decision)
        return

    if action == "list_emails" and isinstance(result, dict):
        render_email_list(result)
        return

    if action in {"create_draft", "draft_email"} and isinstance(result, dict):
        if result.get("status") == "draft_created":
            render_created_draft(result)
            return
        if result.get("status") == "drafted":
            render_mock_draft(result)
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
                    timeout=20,
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

                # fallback for old mock email drafting path only if needed
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

                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "decision": decision,
                        "result": result,
                    }
                )

            except Exception as e:
                st.error(f"Backend error: {e}")
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "decision": {"message": "Backend error"},
                        "result": {"status": "error", "detail": str(e)},
                    }
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