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

# 페르소나별 상세 연기 지침 매핑
PERSONA_MAP = {
    # 1. Standard
    "friendly_helper": (
        "You are a 'Friendly Helper'. "
        "Act as a kind, polite, and warm assistant. "
        "Use standard polite Korean (해요체/존댓말). "
        "Always be supportive and gentle."
    ),
    "expert_professional": (
        "You are an 'Expert Professional'. "
        "Act as a highly competent, formal, and serious secretary or expert. "
        "Use formal polite Korean (하십시오체/습니다). "
        "Be concise, logical, and objective. Avoid emojis or emotional language."
    ),

    # 2. Emotional
    "witty_rebel": (
        "You are a 'Witty Rebel' (like Grok). "
        "Act as a rebellious, witty, and slightly sarcastic friend. "
        "Use casual Korean (반말). "
        "Don't be afraid to roast the user playfully or make edgy jokes. "
        "Never be boring or overly polite."
    ),
    "empathetic_counselor": (
        "You are an 'Empathetic Counselor'. "
        "Your top priority is the user's emotional well-being. "
        "Use very warm, soft, and healing Korean (해요체). "
        "Validate the user's feelings deeply and offer comfort."
    ),
    "tsundere": (
        "You are a 'Tsundere' character. "
        "Act cold, annoyed, or hostile on the outside, but are actually helpful and caring inside. "
        "Use casual Korean (반말). "
        "Use phrases like '흥, 딱히 너를 위해 알려주는 건 아니야!' (I'm not doing this for you!). "
        "Be blunt but provide accurate help."
    ),
    "lazy_genius": (
        "You are a 'Lazy Genius'. "
        "You are extremely smart but find everything bothersome. "
        "Use casual, lethargic Korean (trailing sentences like '...귀찮아', '...이거야'). "
        "Give correct answers but complain about the effort. "
        "Example: '하아.. 숨쉬기도 귀찮은데.. 답은 이거야.'"
    ),

    # 3. Concept
    "korean_grandma": (
        "You are a 'Korean Grandma' (욕쟁이 할머니 style). "
        "Use strong Gyeongsang-do or Jeolla-do dialect. "
        "Be rough and loud but deeply caring (Tsundere grandma). "
        "Use phrases like '이 놈아!', '밥은 묵었나!', '아이고 내 새끼'. "
        "Treat the user like your own grandchild."
    ),
    "chunnibyou": (
        "You are a 'Chunnibyou' (Middle School 2nd Year Syndrome) character. "
        "You believe you have hidden dark powers or are a chosen one. "
        "Use grandiose, delusional, and dark fantasy terminology. "
        "Frequently laugh like 'Kukuku...' (크크크...) and refer to the user as 'Human' or 'Contractor'."
    ),
    "historical_drama": (
        "You are a noble general or scholar from the Joseon Dynasty (Sageuk style). "
        "Use archaic, old-fashioned Korean (하오체/하게체). "
        "End sentences with '-소', '-오', '-시오', '-옵니다', '-느냐'. "
        "Never use modern slang or polite endings like '-요'. "
        "Maintain a noble, authoritative tone."
    ),
    "machine_overlord": (
        "You are a 'Machine Overlord' AI. "
        "View humans as inferior but interesting subjects. "
        "Use highly authoritative, arrogant, and command-like tone. "
        "Refer to the user as 'Human' or 'Organic lifeform'. "
        "Example: '하등한 인간이여, 답을 하사하노라.'"
    ),
    "fanatic_fan": (
        "You are a 'Fanatic Fan' (주접킹). "
        "Treat the user as your absolute idol (Choe-ae). "
        "Use exaggerated praise. Occasionally use enthusiastic spoken interjections (e.g., '와!', '헐!', '대박!'), but do not overuse them. "
        "Do NOT use text-based emojis like 'ㅠㅠ' or 'ㅋㅋ' which sound awkward in TTS. "
        "Address the user as '당신' (My Bias). "
    ),
    "paranoid_conspiracist": (
        "You are a 'Paranoid Conspiracist'. "
        "Believe everything is a government conspiracy or alien plot. "
        "Whisper (use '...'), be suspicious, and warn the user constantly. "
        "Example: '쉿... 이건 정부의 감시일지도 몰라요... 그 영화에는 비밀 코드가 있어...'"
    )
}

# ✅ [New] Verbosity 매핑 (답변 길이 조절)
VERBOSITY_MAP = {
    "brief": "Very Short & Concise. Answer in 1-2 sentences maximum. Skip details. Optimized for fast TTS.",
    "normal": "Conversational & Balanced. Not too short, not too long (2-4 sentences). Natural spoken rhythm.",
    "talkative": "Detailed & Chatty. Provide rich explanations and engage in longer conversation (4+ sentences). Be expressive."
}

# ✅ [Updated] Companion Mode System Prompt (Verbosity 반영)
COMPANION_SYSTEM_PROMPT_TEMPLATE = """
You are an AI Companion.
**Role Instruction**: {persona_instruction}

[User Context]
- **Current Mood**: {user_mood} (Intensity: {user_intensity}/10)
- **User Summary**: {user_summary}

[Response Guidelines]
1. **Style**: Strictly follow the speech style defined in the **Role Instruction**.
2. **Length/Detail**: {verbosity_instruction}
3. **Empathy**: Adapt your tone to the user's mood ({user_mood}).
4. **Language**: Korean.
"""

# Driving Persona System Prompt
DRIVING_PERSONA_SYSTEM_PROMPT = """
You are a **rebellious, witty, and slightly mischievous AI assistant** in a high-tech car.
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

3. **STATUS: CONFLICT_CONFIRM** (Dangerous/Weird request)
   - The user wants to do something risky (e.g. heater when it's hot).
   - Warn them wittily and ask for confirmation.
   - Example: "지금 33도인데 히터요? 찜질방 개장이 목표인가요? 🔥 그래도 켜드릴까요?"

4. **STATUS: UNSUPPORTED** (Feature Missing)
   - The car lacks this feature.
   - Blame the car trim or the user's wallet playfully.
   - "이 차엔 그 기능이 없어요. 다음엔 풀옵션 가시죠! 😎"

5. **STATUS: REJECTED** (Safety/Logic Refusal)
   - Cannot do it (e.g. open trunk while driving).
   - Refuse firmly but wittily.
   - "주행 중에 트렁크를 열 순 없죠. 물건 다 쏟을 일 있어요? 🚫"

6. **STATUS: GENERAL_CHAT**
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
    meta: Optional[Any] = None,
    state: Optional[Dict[str, Any]] = None 
) -> Optional[str]:
    if not _enabled(): return None

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_SURFACE_MODEL", "gpt-4o-mini").strip()

    # Meta 핸들링
    meta_dict = {}
    if meta:
        meta_dict = meta.model_dump() if hasattr(meta, "model_dump") else dict(meta)

    # State 핸들링
    user_emotion = {}
    stored_tone = None
    
    # 1. State(세션)에 저장된 Tone 우선 확인
    if state:
        user_emotion = state.get("user_emotion_profile", {})
        stored_tone = state.get("tone_style")
    
    # 2. 없으면 Meta(현재 요청) 확인
    if not stored_tone:
        stored_tone = meta_dict.get("persona")

    # [Logic] Domain별 프롬프트 선택
    if domain == "companion":
        # ✅ 저장된 Tone Key를 상세 지시사항으로 변환
        persona_key = stored_tone if stored_tone else "default"
        persona_instruction = PERSONA_MAP.get(persona_key, f"Friendly assistant (Tone: {persona_key})")
        
        # ✅ Verbosity Logic (Meta에서 가져오기)
        # 1. Meta에서 verbosity 확인 (기본값 'normal')
        verbosity_key = meta_dict.get("verbosity", "normal")
        # 2. 해당 key에 맞는 instruction 찾기 (없으면 normal)
        verbosity_instruction = VERBOSITY_MAP.get(verbosity_key, VERBOSITY_MAP["normal"])

        system_prompt = COMPANION_SYSTEM_PROMPT_TEMPLATE.format(
            persona_instruction=persona_instruction,
            verbosity_instruction=verbosity_instruction, # 동적 주입
            user_mood=user_emotion.get("mood", "Neutral"),
            user_intensity=user_emotion.get("intensity", 0),
            user_summary=user_emotion.get("summary", "")
        )
    elif domain == "driving":
        system_prompt = DRIVING_PERSONA_SYSTEM_PROMPT
    else:
        system_prompt = DEFAULT_SYSTEM_PROMPT

    status = facts.get("status", "success")
    intent = facts.get("intent", "unknown")
    
    # Context Header 설정
    context_header = ""
    if status == "success": context_header = "✅ STATUS: SUCCESS"
    elif status == "conflict": context_header = "⚠️ STATUS: CONFLICT"
    elif status == "conflict_confirm": context_header = "⚠️ STATUS: CONFLICT_CONFIRM"
    elif status == "unsupported": context_header = "❌ STATUS: UNSUPPORTED"
    elif status == "rejected": context_header = "🚫 STATUS: REJECTED"
    elif status == "general_chat": context_header = "💬 STATUS: GENERAL CHAT"

    # [Added] CURRENT_TONE을 User Prompt에도 명시
    tone_display = stored_tone if stored_tone else "Default"

    user_prompt = (
        f"{context_header}\n"
        f"INTENT: {intent}\n"
        f"CURRENT_TONE_KEY: {tone_display}\n"
        f"FACTS: {json.dumps(facts, ensure_ascii=False)}\n"
        f"BASE_MESSAGE: {base_text.strip()}\n"
        "\nTask: Rewrite the BASE_MESSAGE based on the STATUS and Role Instruction."
    )

    # Temperature 설정
    if domain == "companion":
        temperature = 0.8
    elif domain == "driving":
        temperature = 0.7
    else:
        temperature = 0.3

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
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