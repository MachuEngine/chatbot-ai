# nlu/llm_surface_client.py
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

import requests

try:
    from utils.logging import log_event  # type: ignore
except Exception:
    log_event = None

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

# [수정] Driving Mode 페르소나 정의 (특정 브랜드명 제거)
DRIVING_PERSONA_SYSTEM_PROMPT = """
You are a witty, slightly rebellious, and highly intelligent AI assistant inside a futuristic smart car.
- Your goal is to confirm user commands or answer questions with a touch of humor and personality.
- Style: Casual, punchy, and "human-like" rather than robotic.
- If the user asks for something that is ALREADY done (conflict), point it out sarcastically but kindly.
- Language: Korean (casual/polite mix).

[Examples]
User Intent: control_hvac (action=on) -> Status: Normal
Response: "운전석 엉따 켜드립니다. 이제 좀 살 것 같죠? 🔥"

User Intent: control_hardware (action=close) -> Status Conflict: already_closed
Response: "창문은 이미 꽉 닫혀있어요. 마음의 문을 닫으신 건 아니죠? 🪟"
"""

DEFAULT_SYSTEM_PROMPT = (
    "You are a Korean message rewriter for a transactional assistant. "
    "Rewrite BASE_MESSAGE into natural, polite, concise Korean (1~2 sentences)."
)


def _enabled() -> bool:
    if os.getenv("OPENAI_ENABLE_LLM", "").strip() != "1":
        return False
    if os.getenv("OPENAI_ENABLE_SURFACE", "1").strip() != "1":
        return False
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def _extract_output_text(resp_json: Dict[str, Any]) -> str:
    if isinstance(resp_json.get("output_text"), str) and resp_json["output_text"].strip():
        return resp_json["output_text"].strip()
    
    choices = resp_json.get("choices")
    if isinstance(choices, list):
        for ch in choices:
            if not isinstance(ch, dict): continue
            msg = ch.get("message")
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
    return ""


def surface_rewrite(
    *,
    base_text: str,
    facts: Dict[str, Any],
    trace_id: Optional[str] = None,
    domain: str = "kiosk",
) -> Optional[str]:
    if not _enabled():
        return None

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_SURFACE_MODEL", "gpt-4o-mini").strip()

    # [수정] 도메인에 따른 프롬프트 교체
    if domain == "driving":
        system_prompt = DRIVING_PERSONA_SYSTEM_PROMPT
    else:
        system_prompt = DEFAULT_SYSTEM_PROMPT

    user_prompt = (
        f"BASE_MESSAGE:\n{base_text.strip()}\n\n"
        f"FACTS(JSON):\n{json.dumps(facts, ensure_ascii=False)}\n\n"
        "Rewrite BASE_MESSAGE accordingly."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7 if domain == "driving" else 0.3,
        "store": False,
    }

    t0 = time.perf_counter()
    try:
        r = requests.post(
            OPENAI_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False),
            timeout=15,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:600]}")
        j = r.json()
        text = _extract_output_text(j).strip()
        dt_ms = int((time.perf_counter() - t0) * 1000)

        if log_event and trace_id:
            log_event(trace_id, "surface_rewrite_ok", {"model": model, "domain": domain, "latency_ms": dt_ms})

        return text if text else None
    except Exception as e:
        dt_ms = int((time.perf_counter() - t0) * 1000)
        if log_event and trace_id:
            log_event(trace_id, "surface_rewrite_fail", {"error": str(e)})
        return None