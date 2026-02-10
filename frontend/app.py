import streamlit as st
import requests
from datetime import datetime

API_BASE = "http://127.0.0.1:8000"

st.set_page_config(page_title="ExecAI", layout="centered")
st.title("ExecAI – Executive Assistant (MVP)")
st.caption("Type a request → intent detected → suggested action → pick a time → confirm (mock event).")

user_input = st.text_area(
    "What would you like help with?",
    placeholder="Find a time for all four of us to meet next week",
)

# -----------------------
# SESSION STATE
# -----------------------
if "options" not in st.session_state:
    st.session_state.options = []
if "selected" not in st.session_state:
    st.session_state.selected = None
if "created_event" not in st.session_state:
    st.session_state.created_event = None
if "intent_data" not in st.session_state:
    st.session_state.intent_data = {}
if "decision" not in st.session_state:
    st.session_state.decision = {}

col1, col2 = st.columns(2)

# -----------------------
# BUTTONS
# -----------------------
if col1.button("Run assistant"):
    if not user_input.strip():
        st.warning("Please enter a request.")
    else:
        with st.spinner("Thinking..."):
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

                st.session_state.selected = None
                st.session_state.created_event = None

                if st.session_state.options:
                    st.success("Assistant returned meeting options ✅")
                else:
                    st.info("Assistant ran ✅ (no meeting options for this intent).")

            except Exception as e:
                st.error(f"Backend error: {e}")

if col2.button("Clear"):
    st.session_state.options = []
    st.session_state.selected = None
    st.session_state.created_event = None
    st.session_state.intent_data = {}
    st.session_state.decision = {}
    st.rerun()

# -----------------------
# DEBUG / TRANSPARENCY (capstone-friendly)
# -----------------------
with st.expander("Debug (intent + decision)", expanded=False):
    if st.session_state.intent_data:
        st.markdown("**Detected intent**")
        st.json(st.session_state.intent_data)
    if st.session_state.decision:
        st.markdown("**Decision**")
        st.json(st.session_state.decision)

# -----------------------
# SHOW OPTIONS + SELECTION (only if meeting options exist)
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
# SHOW RESULTS
# -----------------------
if st.session_state.selected:
    st.subheader("Selected slot")
    st.json(st.session_state.selected)

if st.session_state.created_event:
    st.subheader("Created event (mock)")
    st.json(st.session_state.created_event)
