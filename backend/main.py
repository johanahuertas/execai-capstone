from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="ExecAI Backend")


class ParseIntentRequest(BaseModel):
    text: str


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/parse-intent")
def parse_intent(payload: ParseIntentRequest):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required.")

    # Mocked response for Week 1 (no OpenAI yet)
    return {
        "intent": "meeting_scheduling",
        "participants": 4,
        "timeframe": "next week",
        "original_text": text,
    }

