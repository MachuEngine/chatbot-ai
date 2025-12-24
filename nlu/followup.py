# nlu/followup.py
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional, Tuple

from utils.logging import log_event

# ----------------------------
# Heuristic patterns (fallback)
# ----------------------------
# "후속"을 강하게 시사하는 표현 (대표 패턴)
_FOLLOWUP_PAT = re.compile(
    r"^(그럼|그러면|그럼요|그러니까|그래서|근데|근데요|아니|아니요|그리고|또|그거|그거요|그것|이거|이건|저거|저건|방금|아까|전(에)?|이어서|계속|다시)\b"
)

# 참조/지시 대명사(토픽 유지 가능성↑)
_REFERENTIAL_PAT = re.compile(r"(그거|그것|이거|이것|저거|저것|그런|이런|저런|거기|여기|저기)")

def _safe_str(x: Any) -> str:
    return x if isinstance(x, str) else "" if x is None else str(x)

def _last_bot_action(state: Optional[Dict[str, Any]]) -> str:
    if isinstance(state, dict):
        return _safe_str(state.get("last_bot_action"))
    return ""

def heuristic_followup_score(
    user_message: str,
    state: Optional[Dict[str, Any]],
) -> Tuple[float, Dict[str, Any]]:
    """
    0~1 점수.
    높은 점수 = 이전 문맥(topic 등)을 이어갈 가능성 큼.
    """
    msg = (user_message or "").strip()
    reasons: Dict[str, Any] = {}
    score = 0.0

    # 1) 직전이 슬롯 질문이면 followup 가능성 매우 높음
    last_action = _last_bot_action(state)
    if last_action in ("ask_slot", "ask_option_group"):
        score += 0.65
        reasons["last_bot_action_bonus"] = last_action

    # 2) 짧은 답변(예: "응", "맞아", "아니", "네", "아이스로")은 후속일 가능성↑
    if len(msg) <= 10:
        score += 0.20
        reasons["short_msg_bonus"] = len(msg)

    # 3) 문두 후속 표현
    if _FOLLOWUP_PAT.search(msg):
        score += 0.25
        reasons["followup_prefix"] = True

    # 4) 지시/참조 표현
    if _REFERENTIAL_PAT.search(msg):
        score += 0.20
        reasons["referential"] = True

    # 5) "그럼/근데" 류 + 질문형
    if ("?" in msg or msg.endswith("요") or msg.endswith("야") or msg.endswith("냐")) and _FOLLOWUP_PAT.search(msg):
        score += 0.10
        reasons["question_like"] = True

    # clamp
    if score > 1.0:
        score = 1.0
    return score, reasons


# ----------------------------
# LLM followup check (optional)
# ----------------------------
def llm_is_followup(
    user_message: str,
    state: Optional[Dict[str, Any]],
    trace_id: Optional[str] = None,
) -> Optional[Tuple[bool, float, str]]:
    """
    OpenAI SDK가 설치되어 있고, env가 세팅된 경우만 사용.
    실패/미설치면 None 반환.
    """
    if os.getenv("OPENAI_ENABLE_LLM") != "1":
        return None
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None

    # 모델은 followup 전용이 있으면 그걸 쓰고, 없으면 NLU 모델 사용
    model = (os.getenv("OPENAI_FOLLOWUP_MODEL") or os.getenv("OPENAI_NLU_MODEL") or "").strip()
    if not model:
        return None

    # openai SDK 의존성: 너 로그에 openai_sdk_not_installed 뜬 적 있었음
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return None

    # state에서 "현재 topic" 정도만 힌트로
    prev_topic = ""
    if isinstance(state, dict):
        slots = state.get("slots")
        if isinstance(slots, dict):
            t = slots.get("topic")
            if isinstance(t, dict) and "value" in t:
                prev_topic = _safe_str(t.get("value"))
            else:
                prev_topic = _safe_str(t)

    client = OpenAI(api_key=api_key)

    system = (
        "You are a strict dialogue classifier.\n"
        "Decide whether the user's message is a FOLLOW-UP to the previous context/topic.\n"
        "Return JSON only."
    )
    user = {
        "prev_topic": prev_topic,
        "last_bot_action": _last_bot_action(state),
        "user_message": user_message,
        "task": "Return JSON: {\"is_followup\": boolean, \"confidence\": number(0..1), \"reason\": string}"
    }

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            temperature=0,
        )
        content = (resp.choices[0].message.content or "").strip()
        data = json.loads(content)
        is_followup = bool(data.get("is_followup"))
        conf = float(data.get("confidence") or 0.0)
        reason = _safe_str(data.get("reason"))
        return is_followup, max(0.0, min(1.0, conf)), reason
    except Exception as e:
        log_event(trace_id, "followup_llm_fail", {"error": type(e).__name__, "message": str(e)})
        return None


def is_followup(
    user_message: str,
    state: Optional[Dict[str, Any]],
    trace_id: Optional[str] = None,
    threshold: float = 0.55,
) -> Tuple[bool, Dict[str, Any]]:
    """
    최종 followup 판정.
    - LLM 가능하면 LLM 우선
    - 실패하면 heuristic
    """
    llm = llm_is_followup(user_message, state, trace_id=trace_id)
    if llm is not None:
        is_f, conf, reason = llm
        meta = {"provider": "llm", "confidence": conf, "reason": reason}
        return is_f, meta

    score, reasons = heuristic_followup_score(user_message, state)
    meta = {"provider": "heuristic", "score": score, "reasons": reasons, "threshold": threshold}
    return (score >= threshold), meta
