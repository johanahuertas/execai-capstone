# ExecAI – Executive Assistant (MVP)

ExecAI is an end-to-end intelligent assistant that interprets natural language
requests and autonomously plans and executes common executive tasks such as
scheduling meetings or drafting professional emails.

This project focuses on **agent architecture, intent understanding, and orchestration**
rather than full production integrations.

---

## 🚀 What ExecAI Does

Users can type requests such as:

- “Find a time for all four of us to meet tomorrow”
- “Email Sarah the invoice professionally”
- “Remind me to follow up next week”

ExecAI then:
1. Detects the user’s intent using hybrid NLP
2. Extracts structured entities (participants, timeframe, topic, tone, etc.)
3. Decides the appropriate action via an orchestrator
4. Executes the action using mock workflows
5. Returns transparent results to the UI

---

## 🧠 Architecture Overview

### Hybrid NLP (AI-Optional by Design)
- **Rule-based NLP** (always available, free, deterministic)
- **Optional OpenAI LLM** for advanced intent parsing
- Automatic fallback if AI is unavailable (quota, billing, auth, network)

This ensures the assistant **always works**, even without paid AI services.

### Core Components
- **Frontend**: Streamlit UI
- **Backend**: FastAPI
- **Intent Parser**: Hybrid (rules + optional LLM)
- **Orchestrator**: Decides next action based on intent
- **Action Handlers**: Mock email & calendar workflows

---

## 🔁 Example Flow

User Input
↓
Hybrid NLP (Intent + Entities)
↓
Orchestrator Decision
↓
Mock Action (Email / Calendar)
↓
Result Returned to UI

---

## 📬 Supported Intents (MVP)

- `meeting_scheduling`
- `email_drafting`
- `follow_up_reminder`
- `unknown` (safe fallback)

---

## 🧪 Mock Integrations (Intentional)

Calendar and email actions are **mocked** to simulate:
- Meeting availability suggestions
- Event creation
- Email draft generation

### Why Mock?
OAuth-based integrations (Google Workspace, Microsoft Graph) are intentionally
out of scope for this MVP in order to focus on:
- Secure agent design
- Decision-making logic
- Extensibility and explainability

The architecture is designed so real integrations can be added later
without changing the core logic.

---

## 🔍 Transparency & Debugging

The UI includes a **Debug panel** that shows:
- Detected intent and extracted entities
- Decision made by the orchestrator
- Planned or executed action

This makes the agent’s reasoning explicit and auditable.

---

## 🛠 Tech Stack

- Python
- FastAPI
- Streamlit
- Pydantic
- Optional: OpenAI API

---

## 🔮 Future Work (Optional)

- Google Calendar OAuth (read/write)
- Gmail draft & send integration
- Microsoft Graph support
- Persistent user memory
- Task chaining across multiple steps

---

## ✅ Status

This MVP demonstrates a fully working **end-to-end intelligent agent**
with hybrid NLP, decision orchestration, and mock execution flows.

