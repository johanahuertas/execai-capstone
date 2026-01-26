import streamlit as st
import requests

st.set_page_config(page_title="ExecAI", layout="centered")

st.title("ExecAI – Executive Assistant (MVP)")

user_input = st.text_area(
    "What would you like help with?",
    placeholder="Find a time for all four of us to meet next week",
)

if st.button("Submit"):
    if not user_input.strip():
        st.warning("Please enter a request.")
    else:
        with st.spinner("Thinking..."):
            try:
                response = requests.post(
                    "http://127.0.0.1:8000/parse-intent",
                    json={"text": user_input},
                    timeout=10,
                )
                response.raise_for_status()
                st.success("Intent parsed successfully!")
                st.json(response.json())
            except Exception as e:
                st.error(f"Error: {e}")

