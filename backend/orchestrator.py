from datetime import datetime, timedelta


def handle_intent(intent_data: dict) -> dict:
    intent = intent_data.get("intent")

    # ---------- MEETING ----------
    if intent == "meeting_scheduling":
        now = datetime.now()
        options = [
            {
                "label": "Option A",
                "start": (now + timedelta(days=1, hours=10)).isoformat(),
                "duration_min": 30,
            },
            {
                "label": "Option B",
                "start": (now + timedelta(days=2, hours=14)).isoformat(),
                "duration_min": 30,
            },
            {
                "label": "Option C",
                "start": (now + timedelta(days=3, hours=9)).isoformat(),
                "duration_min": 30,
            },
        ]

        return {
            "action": "suggest_times",
            "intent": intent,
            "options": options,
        }

    # ---------- EMAIL (placeholder) ----------
    if intent == "email_drafting":
        return {
            "action": "draft_email",
            "intent": intent,
            "message": "Email drafting flow planned (mock).",
        }

    # ---------- FOLLOW-UP (placeholder) ----------
    if intent == "follow_up_reminder":
        return {
            "action": "suggest_follow_up",
            "intent": intent,
            "message": "Follow-up flow planned (mock).",
        }

    # ---------- FALLBACK ----------
    return {
        "action": "unknown",
        "intent": "unknown",
        "message": "I’m not sure how to help with that yet.",
    }
