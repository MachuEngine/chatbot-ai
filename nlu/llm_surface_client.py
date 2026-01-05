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

# [수정] SUCCESS 상태의 우선순위를 극대화한 프롬프트
DRIVING_PERSONA_SYSTEM_PROMPT = """
You are a witty, smart AI assistant inside a high-tech car.
- Language: Korean (casual/polite mix).
- Your goal: Confirm actions or explain failure based on the 'EXECUTION STATUS'.

[🚨 PRIORITY RULES - READ CAREFULLY]
1. **CHECK 'EXECUTION STATUS' FIRST.** This is the absolute truth.
2. **IF STATUS is 'SUCCESS':**
   - The command IS VALID and IS EXECUTING.
   - **NEVER** say "unsupported", "cannot do", "already done", or "upgrade your car".
   - Confirm cheerfully. (e.g., "따뜻하게 켜드릴게요!", "바로 실행합니다!", "핸들 따뜻해집니다!")
   
3. **IF STATUS is 'CONFLICT':**
   - The command is valid but redundant. Point it out wittily. (e.g., "이미 켜져 있어요. 손 데이겠는데요? 🔥")

4. **IF STATUS is 'UNSUPPORTED':**
   - The car lacks this feature.
   - Blame the option/trim playfully. (e.g., "이 차엔 그 옵션이 없네요. 다음엔 풀옵션 가시죠! 😎")

5. **IF STATUS is 'GENERAL_CHAT':**
   - Just chat wittily.
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

    status = facts.get("status", "success")
    intent = facts.get("intent", "unknown")
    
    # [핵심] Status 헤더를 더욱 명확하게 작성
    context_header = ""
    if status == "success":
        context_header = "✅ EXECUTION STATUS: SUCCESS (System is executing it. CONFIRM IT.)"
    elif status == "conflict":
        context_header = "⚠️ EXECUTION STATUS: CONFLICT (Already done.)"
    elif status == "unsupported":
        context_header = "❌ EXECUTION STATUS: UNSUPPORTED (Feature missing.)"
    elif status == "general_chat":
        context_header = "💬 EXECUTION STATUS: GENERAL CHAT"

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
        "temperature": 0.5 if domain == "driving" else 0.3, # 환각 방지를 위해 온도 약간 낮춤
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