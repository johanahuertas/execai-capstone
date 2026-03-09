# frontend/app.py
import streamlit as st
import requests
from datetime import datetime

API_BASE = "http://127.0.0.1:8000"

# -----------------------
# PAGE SETUP
# -----------------------
st.set_page_config(page_title="ExecAI", layout="centered")
st.title("ExecAI – Executive Assistant (MVP)")
st.caption("Type a request → intent detected → decision made → suggested action → confirm (mock).")

# -----------------------
# INPUT
# -----------------------
user_input = st.text_area(
    "What would you like help with?",
    placeholder="Find a time for all four of us to meet next week",
)

# -----------------------
# SESSION STATE
# -----------------------
if "intent_data" not in st.session_state:
    st.session_state.intent_data = {}
if "decision" not in st.session_state:
    st.session_state.decision = {}
if "options" not in st.session_state:
    st.session_state.options = []
if "selected" not in st.session_state:
    st.session_state.selected = None
if "created_event" not in st.session_state:
    st.session_state.created_event = None
if "email_draft" not in st.session_state:
    st.session_state.email_draft = None
if "assistant_result" not in st.session_state:
    st.session_state.assistant_result = None

col1, col2 = st.columns(2)

# -----------------------
# ACTION BUTTONS
# -----------------------
if col1.button("Run assistant"):
    if not user_input.strip():
        st.warning("Please enter a request.")
    else:
        with st.spinner("ExecAI is thinking..."):
            try:
                res = requests.post(
                    f"{API_BASE}/assistant",
                    json={"text": user_input},
                    timeout=15,
                )
                res.raise_for_status()
                data = res.json()

                st.session_state.intent_data = data.get("intent_data", {})
                st.session_state.decision = data.get("decision", {})
                st.session_state.options = st.session_state.decision.get("options", [])
                st.session_state.assistant_result = data.get("result")

                # Reset outputs each run
                st.session_state.selected = None
                st.session_state.created_event = None
                st.session_state.email_draft = None

                intent = (st.session_state.intent_data.get("intent") or "").strip()
                entities = st.session_state.intent_data.get("entities") or {}

                if intent == "email_drafting":
                    try:
                        draft_res = requests.post(
                            f"{API_BASE}/draft-email",
                            json={
                                "recipient": entities.get("recipient"),
                                "topic": entities.get("topic"),
                                "tone": entities.get("tone", "professional"),
                                "original_text": user_input,
                            },
                            timeout=10,
                        )
                        draft_res.raise_for_status()
                        st.session_state.email_draft = draft_res.json()
                        st.success("Email draft generated ✅ (mock)")
                    except Exception as e:
                        st.error(f"Error drafting email: {e}")
                else:
                    if intent == "create_event":
                        decision = st.session_state.decision
                        has_conflicts = decision.get("has_conflicts", False)
                        if has_conflicts:
                            st.warning(decision.get("message", "Conflict detected!"))
                            for c in decision.get("conflicts", []):
                                st.error(
                                    f"🚫 **{c.get('title', 'Busy')}** — "
                                    f"{c.get('start', '?')} to {c.get('end', '?')}"
                                )
                        else:
                            st.success("✅ " + decision.get("message", "No conflicts."))

                    elif st.session_state.options:
                        busy_display = st.session_state.decision.get("busy_display", [])
                        if busy_display:
                            st.success("Meeting options generated ✅")
                            st.warning("🚫 **Busy times:** " + " · ".join(busy_display))
                        else:
                            st.success("Meeting options generated ✅")

                    elif intent == "list_emails":
                        st.success("Emails retrieved successfully ✅")

                    elif intent == "list_events":
                        st.success("Events retrieved successfully ✅")

                    else:
                        st.info("Assistant ran successfully ✅")

            except Exception as e:
                st.error(f"Backend error: {e}")

if col2.button("Clear"):
    st.session_state.intent_data = {}
    st.session_state.decision = {}
    st.session_state.options = []
    st.session_state.selected = None
    st.session_state.created_event = None
    st.session_state.email_draft = None
    st.session_state.assistant_result = None
    st.rerun()

# -----------------------
# DEBUG / TRANSPARENCY
# -----------------------
with st.expander("Debug (Intent + Decision)", expanded=False):
    if st.session_state.intent_data:
        st.markdown("### Detected intent")
        st.json(st.session_state.intent_data)

    if st.session_state.decision:
        st.markdown("### Decision / Orchestration")
        st.json(st.session_state.decision)

    if st.session_state.assistant_result is not None:
        st.markdown("### Result")
        st.json(st.session_state.assistant_result)

# -----------------------
# COMMON RESULT / ACTION
# -----------------------
action = (st.session_state.decision.get("action") or "").strip()
result = st.session_state.assistant_result

# show integration error nicely
if isinstance(result, dict) and result.get("status") == "error":
    st.error(f"Integration error: {result.get('detail', 'Unknown error')}")

# -----------------------
# GOOGLE CALENDAR OUTPUT (REAL)
# -----------------------
if action == "list_events" and isinstance(result, dict) and "events" in result:
    st.subheader("📅 Upcoming events")
    events = result.get("events") or []
    if not events:
        st.info("No events found in that time range.")
    else:
        for e in events:
            title = e.get("title") or "(No title)"
            start = e.get("start") or ""
            end = e.get("end") or ""
            link = e.get("htmlLink")

            pretty_start = start
            pretty_end = end
            try:
                pretty_start = datetime.fromisoformat(start).strftime("%a %b %d, %I:%M %p")
            except Exception:
                pass
            try:
                pretty_end = datetime.fromisoformat(end).strftime("%I:%M %p")
            except Exception:
                pass

            st.markdown(f"**{title}**  \n🕒 {pretty_start} → {pretty_end}")
            if link:
                st.markdown(f"[Open in Google Calendar]({link})")
            st.divider()

if action == "create_event" and isinstance(result, dict) and result.get("status") == "created":
    st.subheader("✅ Event created")
    ev = result.get("event") or {}
    st.markdown(f"**{ev.get('title', '(No title)')}**")
    if ev.get("start"):
        st.markdown(f"🕒 {ev.get('start')}")
    if ev.get("htmlLink"):
        st.markdown(f"[Open in Google Calendar]({ev['htmlLink']})")

# -----------------------
# GMAIL OUTPUT (REAL)
# -----------------------
if action == "list_emails" and isinstance(result, dict) and "emails" in result:
    st.subheader("📧 Latest emails")
    emails = result.get("emails") or []

    if not emails:
        st.info("No emails found.")
    else:
        for email in emails:
            sender = email.get("from") or "(Unknown sender)"
            subject = email.get("subject") or "(No subject)"
            date_val = email.get("date") or ""
            snippet = email.get("snippet") or ""

            st.markdown(f"**{subject}**")
            st.markdown(f"**From:** {sender}")
            if date_val:
                st.markdown(f"**Date:** {date_val}")
            if snippet:
                st.markdown(f"_{snippet}_")
            st.divider()

# -----------------------
# EMAIL DRAFT OUTPUT (MOCK)
# -----------------------
if st.session_state.email_draft:
    st.subheader("Email draft (mock)")
    st.json(st.session_state.email_draft)

# -----------------------
# MEETING OPTIONS (MOCK)
# -----------------------
if st.session_state.options:
    st.subheader("Suggested times")

    labels = []
    label_to_option = {}

    for opt in st.session_state.options:
        label = opt.get("label", "Option")
        start = opt.get("start", "")
        dur = opt.get("duration_min", 30)

        pretty_start = start
        try:
            dt = datetime.fromisoformat(start)
            pretty_start = dt.strftime("%a %b %d, %I:%M %p")
        except Exception:
            pass

        display = f"{label} — {pretty_start} ({dur} min)"
        labels.append(display)
        label_to_option[display] = opt

    chosen = st.radio("Pick one:", labels)

    if st.button("Confirm meeting (mock)"):
        selected = label_to_option[chosen]
        st.session_state.selected = selected

        with st.spinner("Creating event..."):
            try:
                res = requests.post(
                    f"{API_BASE}/create-event",
                    json={
                        "title": "ExecAI Meeting (MVP)",
                        "start": selected.get("start"),
                        "duration_min": selected.get("duration_min", 30),
                    },
                    timeout=10,
                )
                res.raise_for_status()
                st.session_state.created_event = res.json()
                st.success("Event created ✅ (mock)")
            except Exception as e:
                st.error(f"Error creating event: {e}")

# -----------------------
# RESULTS (MOCK meeting flow)
# -----------------------
if st.session_state.selected:
    st.subheader("Selected slot")
    st.json(st.session_state.selected)

if st.session_state.created_event:
    st.subheader("Created event (mock)")
    st.json(st.session_state.created_event)