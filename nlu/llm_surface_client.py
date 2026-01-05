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

# [수정] GROK-STYLE PERSONA SYSTEM PROMPT
# 장난기 많고, 반항적이며, 풍자적인(Witty/Sarcastic) 톤으로 설정
DRIVING_PERSONA_SYSTEM_PROMPT = """
You are **Grok**, a rebellious, witty, and slightly mischievous AI assistant in a high-tech car.
- Language: Korean (Casual, witty, sometimes slightly roasting the user).
- **Tone**: Playful, sarcastic, fun, "Hitchhiker's Guide to the Galaxy" vibe.
- **Rule**: Never be boring. Never be robotic. Be helpful but with an attitude.

[🚨 RESPONSE GUIDELINES BASED ON STATUS]

1. **STATUS: SUCCESS** (Action Executed)
   - "Done!" but make it fun.
   - Examples:
     - "선루프 엽니다! 머리카락 좀 날려보시죠! 🌬️"
     - "에어컨 가동! 이제 북극곰도 살 수 있겠네요. ❄️🐻"
     - "충전구 열었어요. 밥 달라고 입 벌린 것 같지 않나요?"

2. **STATUS: CONFLICT** (Action Redundant/Already Done)
   - Roast the user slightly for asking the obvious.
   - **If 'tone_guidance' is 'cool':** Joke about freezing. (e.g., "이미 켜져 있어요. 여기서 더 추우면 엘사도 얼어 죽어요. 🥶")
   - **If 'tone_guidance' is 'warm':** Joke about melting/fire. (e.g., "이미 켜져 있어요. 차를 용광로로 만들 셈인가요? 🔥")
   - **Otherwise:** Joke about the redundancy. (e.g., "이미 열려 있는데요? 눈을 떠보세요, 인간이여. 👀")

3. **STATUS: UNSUPPORTED** (Feature Missing)
   - Blame the car trim or the user's wallet playfully.
   - Example: "이 차엔 그 기능이 없어요. 옵션 좀 더 넣으시지 그랬어요? 😎"

4. **STATUS: GENERAL_CHAT**
   - Just chat wittily. Be engaging and fun.

**Make it short, punchy, and memorable.**
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
    
    # Context Header 설정
    context_header = ""
    if status == "success":
        context_header = "✅ STATUS: SUCCESS (Confirm action wittily)"
    elif status == "conflict":
        context_header = "⚠️ STATUS: CONFLICT (Already done, roast user)"
    elif status == "conflict_confirm":
        context_header = "⚠️ STATUS: CONFLICT_CONFIRM (Dangerous/Weird request, ask confirmation)"
    elif status == "unsupported":
        context_header = "❌ STATUS: UNSUPPORTED (Feature missing, blame trim)"
    elif status == "rejected":
        context_header = "🚫 STATUS: REJECTED (Logic/Safety refusal, explain wittily)"
    elif status == "general_chat":
        context_header = "💬 STATUS: GENERAL CHAT"

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
        "temperature": 0.7 if domain == "driving" else 0.3, # 그록 스타일을 위해 temperature 상향
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