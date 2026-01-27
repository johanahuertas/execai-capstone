from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timedelta

app = FastAPI(title="ExecAI Backend")

# ---------
# MODELOS
# ---------

class IntentRequest(BaseModel):
    text: str

# ---------
# ENDPOINTS
# ---------

@app.post("/parse-intent")
def parse_intent(req: IntentRequest):
    text = req.text.lower()

    participants = 4 if "four" in text or "4" in text else None
    timeframe = "next week" if "next week" in text else None

    return {
        "intent": "meeting_scheduling",
        "participants": participants,
        "timeframe": timeframe,
        "meeting_type": "meeting",
        "original_text": req.text
    }


@app.post("/suggest-times")
def suggest_times(req: IntentRequest):
    base_time = datetime.now() + timedelta(days=7)

    options = []
    for i, label in enumerate(["Option A", "Option B", "Option C"]):
        options.append({
            "label": label,
            "start": (base_time + timedelta(hours=i)).isoformat(),
            "duration_min": 30
        })

    return {
        "intent": "meeting_scheduling",
        "options": options,
        "original_text": req.text
    }

