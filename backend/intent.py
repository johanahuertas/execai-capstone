# backend/intent.py
import os
import json
from typing import Dict, Any
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
You are an intent parsing assistant for an executive assistant app.
Extract the user's intent and a few structured fields.
Return ONLY valid JSON (no markdown).

Schema:
{
  "intent": "meeting_scheduling" | "other",
  "participants": number | null,
  "timeframe": string | null,
  "meeting_type": string | null,
  "original_text": string
}
"""


def parse_intent(text: str) -> Dict[str, Any]:
    """
    Calls OpenAI to parse user intent into a structured JSON dict.
    """
    if not text or not text.strip():
        return {
            "intent": "other",
            "participants": None,
            "timeframe": None,
            "meeting_type": None,
            "original_text": text or "",
        }

    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.strip()},
            {"role": "user", "content": text.strip()},
        ],
        response_format={"type": "json_object"},
    )

    try:
        parsed = json.loads(resp.choices[0].message.content)
    except Exception:
        parsed = {
            "intent": "other",
            "participants": None,
            "timeframe": None,
            "meeting_type": None,
            "original_text": text.strip(),
        }

    parsed["original_text"] = parsed.get("original_text") or text.strip()
    return parsed
