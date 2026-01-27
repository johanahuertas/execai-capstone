import streamlit as st
import requests
from datetime import datetime

API_BASE = "http://127.0.0.1:8000"

st.set_page_config(page_title="ExecAI", layout="centered")
st.title("ExecAI – Executive Assistant (MVP)")
st.caption("Type a request → get 3 suggested meeting times → pick one → confirm.")

user_input = st.text_area(
    "What would you like help with?",
    placeholder="Find a time for all four of us to meet next week",
)

if "options" not in st.session_state:
    st.session_state.options = []
if "selected" not in st.session_state:
    st.session_state.selected = None

col1, col2 = st.columns(2)

if col1.button("Suggest times"):
    if not user_input.strip():
        st.warning("Please enter a request.")
    else:
        with st.spinner("Getting suggested time slots..."):
            try:
                res = requests.post(
                    f"{API_BASE}/suggest-times",
                    json={"text": user_input},
                    timeout=10,
                )
                res.raise_for_status()
                data = res.json()
                st.session_state.options = data.get("options", [])
                st.session_state.selected = None

                if st.session_state.options:
                    st.success("Suggested times loaded ✅")
                else:
                    st.warning("No options returned.")
            except Exception as e:
                st.error(f"Backend error: {e}")

if col2.button("Clear"):
    st.session_state.options = []
    st.session_state.selected = None
    st.rerun()

# Show options
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

    if st.button("Confirm meeting"):
        st.session_state.selected = label_to_option[chosen]
        st.success("Meeting confirmed (mock) ✅")

if st.session_state.selected:
    st.subheader("Confirmed slot")
    st.json(st.session_state.selected)

