# ExecAI -- Executive Assistant (MVP)

ExecAI is an end-to-end intelligent assistant that interprets natural
language requests and coordinates common executive tasks such as
scheduling meetings and drafting professional emails.

The project focuses on the architecture of intelligent assistants,
particularly intent detection, orchestration logic, and integration with
external services. Rather than emphasizing complex machine learning
models, the system highlights clear system design, reliable task
execution, and transparent decision making.

------------------------------------------------------------------------

## Overview

ExecAI allows a user to interact with an assistant through natural
language. The assistant interprets the request, extracts structured
information, determines the appropriate action, and executes the task
through integrated services.

Example requests include:

- Find a time for all four of us to meet tomorrow
- Email Sarah the invoice professionally
- Reply to my latest email and schedule a meeting
- Show my calendar for next week
- Draft an email to a colleague about a proposal

Each request is processed through several stages:

1. Intent detection and entity extraction
2. Decision making through an orchestration layer
3. Execution through the appropriate integration
4. Returning structured results to the user interface

------------------------------------------------------------------------

## System Architecture

The system is composed of a lightweight user interface and a backend
service responsible for interpreting requests and coordinating actions.

![ExecAI Architecture](docs/execai_system_architecture.png)

### Frontend

The frontend is implemented using Streamlit. It provides a
conversational interface where users can submit requests and view
results returned by the assistant.

The interface also includes an optional debugging panel that exposes
internal system reasoning such as detected intents and orchestration
decisions.

### Backend

The backend is implemented with FastAPI. It exposes API endpoints that
receive user requests and coordinate assistant operations.

The backend is responsible for:

- Processing requests
- Detecting intents
- Extracting entities
- Orchestrating actions
- Communicating with external services

### Intent Parser

The intent parser analyzes user input and determines the user's goal. It
extracts structured information such as:

- Participants
- Timeframes
- Topics
- Email tone
- Email recipients
- Event titles
- Duration

This structured representation allows the system to convert natural
language into executable actions.

### Orchestrator

The orchestrator acts as the decision engine of the assistant. Based on
the detected intent and extracted entities, it determines which action
should be executed and which integration should be invoked.

This component ensures that the assistant behaves predictably and that
different capabilities such as email, scheduling, and suggestion flows
are handled through a unified decision layer.

### Availability Engine

The availability module evaluates calendar availability and detects
scheduling conflicts. When a requested meeting time is unavailable, the
system generates alternative meeting slots.

### Service Integrations

ExecAI integrates with external services in order to execute actions:

- Google Calendar API for event creation, event listing, and availability checks
- Gmail API for reading emails and generating drafts
- Google OAuth for secure account connection from the UI

------------------------------------------------------------------------

## Execution Flow

The following diagram illustrates how a request moves through the
system.

![ExecAI Sequence](docs/execai_sequence_diagram.png)

A typical interaction proceeds as follows:

1. The user submits a request through the Streamlit interface.
2. The frontend sends the request to the FastAPI backend.
3. The intent parser analyzes the request and extracts structured data.
4. The orchestrator determines the required action.
5. The availability module checks for conflicts when scheduling.
6. The appropriate integration (Calendar or Gmail) executes the action.
7. The result is returned to the frontend and displayed to the user.

------------------------------------------------------------------------

## Agent Decision Flow

The assistant uses a decision flow that routes requests depending on the
detected intent. This ensures that each user request is handled by the
appropriate workflow.

![ExecAI Decision Flow](docs/execai_decision_flow.png)

The decision flow includes:

- Calendar actions (list events, create events)
- Email actions (read email, draft email, reply)
- Combined workflows such as replying to an email and scheduling a
  meeting
- Combined workflows such as drafting an email and creating a meeting
- Fallback responses when the intent cannot be determined

------------------------------------------------------------------------

## Supported Capabilities

### Calendar

- List upcoming events
- Create calendar events
- Detect scheduling conflicts
- Suggest alternative meeting times
- Check free/busy availability

### Email

- List recent emails
- Read email content
- Draft new emails
- Generate reply drafts
- Reply to emails and schedule meetings
- Draft an email and create a meeting in one flow

### UI / Demo Features

- Google OAuth connect flow from the Streamlit sidebar
- Connection status indicator for Google account
- Demo panel for upcoming meetings
- Demo panel for free time tomorrow
- Demo panel for quick Gmail draft creation
- Chat-based assistant interface for natural language requests

------------------------------------------------------------------------

## Transparency and Debugging

ExecAI includes an optional debugging panel that exposes the assistant's
internal reasoning. This allows developers to inspect:

- Detected intent
- Extracted entities
- Orchestrator decisions
- Execution results

This transparency helps ensure that the system remains understandable
and traceable during development and testing.

------------------------------------------------------------------------

## Technology Stack

### Backend

- Python
- FastAPI
- Pydantic

### Frontend

- Streamlit

### Integrations

- Gmail API
- Google Calendar API
- Google OAuth 2.0

------------------------------------------------------------------------

## Project Structure

```text
execai/
│
├── backend/
│   ├── main.py
│   ├── orchestrator.py
│   ├── intent.py
│   ├── availability.py
│   ├── integrations.py
│   └── .tokens/
│
├── frontend/
│   └── app.py
│
├── docs/
│   ├── execai_system_architecture.png
│   ├── execai_sequence_diagram.png
│   └── execai_decision_flow.png
│
├── .env
├── requirements.txt
└── README.md
```

------------------------------------------------------------------------

## Local Setup

### 1. Clone the repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd execai
```

### 2. Create a virtual environment

#### macOS / Linux

```bash
python3 -m venv venv
source venv/bin/activate
```

#### Windows

```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Install dependencies

If a `requirements.txt` file is available:

```bash
pip install -r requirements.txt
```

Otherwise install the required packages manually:

```bash
pip install fastapi uvicorn streamlit requests python-dotenv
```

------------------------------------------------------------------------

## Environment Variables

Create a `.env` file in the root of the project:

```env
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/integrations/google/callback
```

### Notes

- `GOOGLE_REDIRECT_URI` must exactly match the redirect URI configured in
  your Google Cloud OAuth settings.
- The `.env` file should stay local and should not be committed to GitHub.
- Team members who want to test locally need these credentials in their
  own `.env` file.

------------------------------------------------------------------------

## Google OAuth Setup

To use Gmail and Google Calendar integrations, the project must be
connected to a Google Cloud OAuth application.

### Required configuration in Google Cloud

1. Create or use an existing Google Cloud project
2. Enable:
   - Gmail API
   - Google Calendar API
3. Configure the OAuth consent screen
4. Add test users if the app is still in testing mode
5. Add the redirect URI:

```text
http://127.0.0.1:8000/integrations/google/callback
```

### Test Users

If the OAuth app is in testing mode, only users added under Google Cloud
OAuth **Test Users** can authorize the app.

------------------------------------------------------------------------

## Running the App Locally

### Start the backend

From the project root:

```bash
uvicorn backend.main:app --reload
```

The backend will run at:

```text
http://127.0.0.1:8000
```

### Start the frontend

Open a second terminal in the same project root and run:

```bash
streamlit run frontend/app.py
```

The frontend will run at:

```text
http://localhost:8501
```

------------------------------------------------------------------------

## Connecting a Google Account

Once both backend and frontend are running:

1. Open the Streamlit UI
2. In the sidebar, click **Connect Google**
3. Follow the Google authorization flow
4. After authorization, the callback page will confirm that Google was connected
5. Return to the app and continue testing

After a successful connection, ExecAI can use the connected user's:

- Gmail
- Google Calendar

------------------------------------------------------------------------

## Example Prompts

### Calendar

- Show my calendar for next week
- Create a meeting with sarah@example.com tomorrow at 11am
- Schedule a budget review with sarah@example.com and john@example.com tomorrow at 2pm for 45 minutes
- Find a time for all four of us to meet tomorrow

### Email

- Show my latest emails
- Read my latest email
- Reply to my latest email saying "Thanks for the update"
- Draft an email to sarah@example.com about the proposal

### Combined Workflows

- Reply to my latest email saying "I am available tomorrow at 2pm" and create the meeting
- Draft an email to sarah@example.com saying "I am available tomorrow at 2pm" and create the meeting

------------------------------------------------------------------------

## Demo Workflow

A simple demo flow for presenting the project:

1. Connect Google account from the sidebar
2. Show upcoming meetings
3. Check free time tomorrow
4. Create a Gmail draft from the quick demo panel
5. Use the assistant chat for:
   - scheduling
   - email drafting
   - email replies
   - combined workflows

------------------------------------------------------------------------

## Main Endpoints

### Assistant / Core

- `GET /health`
- `POST /parse-intent`
- `POST /assistant`

### Integrations

- `GET /integrations/status`
- `GET /integrations/google/auth-url`
- `GET /integrations/google/callback`
- `POST /integrations/google/list-events`
- `POST /integrations/google/create-event`
- `POST /integrations/google/freebusy`
- `GET /integrations/google/list-emails`
- `GET /integrations/google/read-email/{message_id}`
- `POST /integrations/google/create-draft`
- `POST /integrations/google/create-reply-draft`

------------------------------------------------------------------------

## Development Notes

- OAuth tokens are stored locally in `backend/.tokens/`
- The project currently targets local development and demo use
- The assistant supports both:
  - rule-based intent parsing
  - optional LLM-based parsing when configured
- If AI credentials are not available, the rule-based system still works

------------------------------------------------------------------------

## Troubleshooting

### Google is not connected

- Check that the backend is running
- Check that the `.env` file exists
- Check that OAuth credentials are correct
- Reconnect using the sidebar button

### Redirect URI mismatch

Make sure the redirect URI in Google Cloud exactly matches:

```text
http://127.0.0.1:8000/integrations/google/callback
```

### Access denied during Google login

If the app is still in testing mode, make sure your email is included in
Google OAuth **Test Users**.

### Freebusy or Gmail requests fail

- Make sure the Google account was connected successfully
- Make sure the relevant Google APIs are enabled
- Make sure the OAuth scopes include Calendar and Gmail access

------------------------------------------------------------------------

## Future Improvements

Potential extensions for the system include:

- Support for additional email and calendar providers
- Multi-step task planning
- User preference memory
- Authentication and multi-user support
- Deployment as a hosted service
- Better user management for team-wide shared testing
- Improved production credential handling
- More advanced natural language understanding

------------------------------------------------------------------------

## Status

ExecAI demonstrates a functional intelligent assistant architecture with
intent detection, decision orchestration, and integration with real
calendar and email services. The project highlights how natural language
interfaces can coordinate multiple workflows within a unified assistant
system.

The current MVP supports real Gmail and Google Calendar integration,
local OAuth connection from the UI, conflict-aware event creation, and
natural language workflows across email and scheduling.
