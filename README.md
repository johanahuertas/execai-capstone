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

-   Find a time for all four of us to meet tomorrow
-   Email Sarah the invoice professionally
-   Reply to my latest email and schedule a meeting
-   Show my calendar for next week
-   Draft an email to a colleague about a proposal

Each request is processed through several stages:

1.  Intent detection and entity extraction\
2.  Decision making through an orchestration layer\
3.  Execution through the appropriate integration\
4.  Returning structured results to the user interface

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

The backend is implemented with FastAPI. It exposes an API endpoint that
receives user requests and coordinates all assistant operations.

The backend is responsible for:

-   Processing requests
-   Detecting intents
-   Extracting entities
-   Orchestrating actions
-   Communicating with external services

### Intent Parser

The intent parser analyzes user input and determines the user's goal. It
extracts structured information such as:

-   Participants
-   Timeframes
-   Topics
-   Email tone

This structured representation allows the system to convert natural
language into executable actions.

### Orchestrator

The orchestrator acts as the decision engine of the assistant. Based on
the detected intent and extracted entities, it determines which action
should be executed and which integration should be invoked.

This component ensures that the assistant behaves predictably and that
different capabilities (email, scheduling, suggestions) are handled
through a unified decision layer.

### Availability Engine

The availability module evaluates calendar availability and detects
scheduling conflicts. When a requested meeting time is unavailable, the
system generates alternative meeting slots.

### Service Integrations

ExecAI integrates with external services in order to execute actions:

-   Google Calendar API for event creation and scheduling
-   Gmail API for reading emails and generating drafts

------------------------------------------------------------------------

## Execution Flow

The following diagram illustrates how a request moves through the
system.

![ExecAI Sequence](docs/execai_sequence_diagram.png)

A typical interaction proceeds as follows:

1.  The user submits a request through the Streamlit interface.
2.  The frontend sends the request to the FastAPI backend.
3.  The intent parser analyzes the request and extracts structured data.
4.  The orchestrator determines the required action.
5.  The availability module checks for conflicts when scheduling.
6.  The appropriate integration (Calendar or Gmail) executes the action.
7.  The result is returned to the frontend and displayed to the user.

------------------------------------------------------------------------

## Agent Decision Flow

The assistant uses a decision flow that routes requests depending on the
detected intent. This ensures that each user request is handled by the
appropriate workflow.

![ExecAI Decision Flow](docs/execai_decision_flow.png)

The decision flow includes:

-   Calendar actions (list events, create events)
-   Email actions (read email, draft email, reply)
-   Combined workflows such as replying to an email and scheduling a
    meeting
-   Fallback responses when the intent cannot be determined

------------------------------------------------------------------------

## Supported Capabilities

### Calendar

-   List upcoming events
-   Create calendar events
-   Detect scheduling conflicts
-   Suggest alternative meeting times

### Email

-   List recent emails
-   Read email content
-   Draft new emails
-   Generate reply drafts
-   Reply to emails and schedule meetings

------------------------------------------------------------------------

## Transparency and Debugging

ExecAI includes an optional debugging panel that exposes the assistant's
internal reasoning. This allows developers to inspect:

-   Detected intent
-   Extracted entities
-   Orchestrator decisions
-   Execution results

This transparency helps ensure that the system remains understandable
and traceable during development and testing.

------------------------------------------------------------------------

## Technology Stack

Backend

-   Python
-   FastAPI
-   Pydantic

Frontend

-   Streamlit

Integrations

-   Gmail API
-   Google Calendar API

------------------------------------------------------------------------

## Project Structure

execai

backend\
main.py\
orchestrator.py\
intent.py\
availability.py\
integrations.py

frontend\
app.py

docs\
execai_system_architecture.png\
execai_sequence_diagram.png\
execai_decision_flow.png

README.md

------------------------------------------------------------------------

## Future Improvements

Potential extensions for the system include:

-   Support for additional email and calendar providers
-   Multi-step task planning
-   User preference memory
-   Authentication and multi-user support
-   Deployment as a hosted service

------------------------------------------------------------------------

## Status

ExecAI demonstrates a functional intelligent assistant architecture with
intent detection, decision orchestration, and integration with real
calendar and email services. The project highlights how natural language
interfaces can coordinate multiple workflows within a unified assistant
system.
