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
                    timeout=10,
                )
                res.raise_for_status()
                data = res.json()

                st.session_state.intent_data = data.get("intent_data", {})
                st.session_state.decision = data.get("decision", {})
                st.session_state.options = st.session_state.decision.get("options", [])

                # Reset outputs each run
                st.session_state.selected = None
                st.session_state.created_event = None
                st.session_state.email_draft = None

                # If intent is email, call /draft-email (mock) and show the draft
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
                    # Show conflict warnings for create_event
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

                    # Show busy times for meeting scheduling
                    elif st.session_state.options:
                        busy_display = st.session_state.decision.get("busy_display", [])
                        if busy_display:
                            st.success("Meeting options generated ✅")
                            st.warning("🚫 **Busy times:** " + " · ".join(busy_display))
                        else:
                            st.success("Meeting options generated ✅")

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

# -----------------------
# EMAIL DRAFT OUTPUT
# -----------------------
if st.session_state.email_draft:
    st.subheader("Email draft (mock)")
    st.json(st.session_state.email_draft)

# -----------------------
# MEETING OPTIONS
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
# RESULTS
# -----------------------
if st.session_state.selected:
    st.subheader("Selected slot")
    st.json(st.session_state.selected)

if st.session_state.created_event:
    st.subheader("Created event (mock)")
    st.json(st.session_state.created_event)
