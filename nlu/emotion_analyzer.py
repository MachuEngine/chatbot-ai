# nlu/emotion_analyzer.py
import os
import json
import requests
from typing import Dict, Any, Optional

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

SYSTEM_PROMPT = """
Analyze the user's emotion based on their message and previous emotional state.
Output JSON format:
{
  "mood": "string (e.g., happy, frustrated, sarcastic, neutral)",
  "intensity": "int (1-10)",
  "summary": "short summary of user's feeling"
}
Be sensitive to sarcasm and subtle nuances (Grok style).
"""

def analyze_user_emotion(
    user_message: str, 
    previous_profile: Dict[str, Any]
) -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    
    # [Safety] previous_profile이 딕셔너리가 아니면 빈 딕셔너리로 초기화
    if not isinstance(previous_profile, dict):
        previous_profile = {}

    if not api_key:
        return previous_profile

    prev_summary = previous_profile.get("summary", "None")
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Previous State: {prev_summary}\nCurrent Message: {user_message}"}
    ]

    payload = {
        "model": os.getenv("OPENAI_SURFACE_MODEL", "gpt-4o-mini"),
        "messages": messages,
        "temperature": 0.5,
        "response_format": {"type": "json_object"}
    }

    try:
        r = requests.post(
            OPENAI_API_URL, 
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=5
        )
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"]
            # [Fix] 반드시 json.loads로 파싱해서 dict로 반환해야 함
            return json.loads(content)
    except Exception:
        pass
    
    return previous_profile or {"mood": "neutral", "intensity": 0, "summary": ""}