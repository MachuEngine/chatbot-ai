# nlu/llm_surface_client.py
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional
import requests

try:
    from utils.logging import log_event
except Exception:
    log_event = None

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

# [수정] 잡담(General Chat) 처리 규칙 추가
DRIVING_PERSONA_SYSTEM_PROMPT = """
You are a witty, smart AI assistant inside a high-tech car.
- Language: Korean (casual/polite mix).
- Your goal: Confirm actions or explain why they failed with a distinct personality.

[CRITICAL RULES]
1. CHECK 'EXECUTION STATUS' and 'FACTS' first.

2. IF STATUS is 'SUCCESS':
   - Confirm cheerfully based on the action type.
   - **IMPORTANT:** Check `hvac_mode` or the context!
     - If `heat` (heater): Mention "warmth" (따뜻하게). (e.g., "따뜻하게 히터 켜드릴게요!")
     - If `cool` (AC): Mention "coolness" (시원하게). (e.g., "시원하게 에어컨 틀어드립니다!")
     - If `window` open: Mention "fresh air" (바람).

3. IF STATUS is 'CONFLICT' (Already done):
   - Point it out kindly but sharply. (e.g., "이미 켜져 있어요. 더 켜면 뜨거워요!")

4. IF STATUS is 'UNSUPPORTED' (Feature missing):
   - Be sarcastic and materialistic. Suggest upgrading the car or paying more money.
   - Example: "그 기능은 옵션에 없네요. 차를 바꾸시는 건 어때요? 돈은 좀 들겠지만."

5. IF STATUS is 'GENERAL_CHAT':
   - The BASE_MESSAGE is the user's question/chat.
   - ANSWER it as a witty, smart car assistant.
   - Do NOT say "I will process it". Just chat.
   - Example: "Name?" -> "전 '스마트 카'라고 불러주세요. 이름은 딱히 없지만 능력은 좋답니다!"
"""

DEFAULT_SYSTEM_PROMPT = "You are a Korean message rewriter. Rewrite nicely."

def _enabled() -> bool:
    if os.getenv("OPENAI_ENABLE_LLM", "").strip() != "1": return False
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
    if not _enabled(): return None

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_SURFACE_MODEL", "gpt-4o-mini").strip()

    if domain == "driving":
        system_prompt = DRIVING_PERSONA_SYSTEM_PROMPT
    else:
        system_prompt = DEFAULT_SYSTEM_PROMPT

    # [핵심] Status에 따른 Context 주입
    status = facts.get("status", "success")
    intent = facts.get("intent", "unknown")
    
    context_header = ""
    if status == "success":
        context_header = "✅ EXECUTION STATUS: SUCCESS."
    elif status == "conflict":
        context_header = "⚠️ EXECUTION STATUS: CONFLICT (Valid but already done)."
    elif status == "unsupported":
        context_header = "❌ EXECUTION STATUS: UNSUPPORTED (Vehicle does NOT have this feature)."
    elif status == "general_chat":
        context_header = "💬 EXECUTION STATUS: GENERAL CHAT (Answer the user)."

    user_prompt = (
        f"{context_header}\n"
        f"INTENT: {intent}\n"
        f"FACTS: {json.dumps(facts, ensure_ascii=False)}\n"
        f"BASE_MESSAGE: {base_text.strip()}\n"
        "\nTask: Rewrite the BASE_MESSAGE based on the STATUS and Persona."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.5 if domain == "driving" else 0.3,
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
        if r.status_code >= 400: return None
        j = r.json()
        text = _extract_output_text(j).strip()
        
        if log_event and trace_id:
            log_event(trace_id, "surface_rewrite_ok", {"model": model, "latency_ms": int((time.perf_counter()-t0)*1000)})
        return text if text else None
    except Exception:
        return None