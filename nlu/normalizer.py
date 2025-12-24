# nlu/normalizer.py
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from utils.logging import log_event
from nlu.followup import is_followup, _REFERENTIAL_PAT  # _REFERENTIAL_PAT 재사용

def _now_ts() -> float:
    return time.time()

def _safe_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}

def _safe_str(x: Any) -> str:
    return x if isinstance(x, str) else "" if x is None else str(x)

def _merge_dict(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """
    b가 우선.
    단, b의 값이 None이면 skip (기존값 유지)
    """
    out = dict(a or {})
    for k, v in (b or {}).items():
        if v is None:
            continue
        out[k] = v
    return out

def _slot_value(slot: Any) -> Any:
    # {"value":..., "confidence":...} -> value
    if isinstance(slot, dict) and "value" in slot:
        return slot.get("value")
    return slot

def _slot_conf(slot: Any) -> float:
    if isinstance(slot, dict) and "confidence" in slot and isinstance(slot.get("confidence"), (int, float)):
        return float(slot.get("confidence"))
    return 0.0

def _has_nonempty(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) > 0
    return True

def _normalize_text_for_match(s: str) -> str:
    # 토픽 근거성 체크용: 공백/개행 단순 정리
    return re.sub(r"\s+", " ", s.strip())

def _last_bot_action(state: Optional[Dict[str, Any]]) -> str:
    if isinstance(state, dict):
        return _safe_str(state.get("last_bot_action"))
    return ""

# ---------
# 정책 키들
# ---------
# education에서 "유지해도 되는 사용자 선호" 슬롯들
EDU_PREFERENCE_KEYS = {
    "level", "subject", "style", "include_examples", "example_type", "language", "length", "tone",
    # 너가 주석으로 남겼던 것처럼 target_improvements는 선호/요구에 가깝게 유지하는 편이 안전
    "target_improvements",
}

# education에서 "컨텍스트성" 슬롯들: followup이 아니면 끊는 대상
EDU_CONTEXT_KEYS = {
    "topic",
}

# -----
# 중요: 여기서만 import (re) 사용
# -----
import re


def _should_keep_new_topic_when_not_followup(
    user_message: str,
    topic_new_val: Any,
    slots_in: Dict[str, Any],
) -> bool:
    """
    followup == False 인 상황에서,
    NLU가 새 topic을 넣어도 '근거가 있으면' 유지, 없으면 제거.

    근거 기준(보수적으로):
    - 지시/참조(그거/이거/방금/아까/이어서...)가 있으면 문맥 참조 가능성이 있으니 유지
    - user_message 텍스트에 topic 문자열이 직접 포함되면 유지
    - include_examples 등과 같이 명시적 교육 요청 패턴이 있고 topic도 같이 들어온 경우는 유지 (옵션)
      (여기서는 최소 규칙만 적용)
    """
    msg = (user_message or "").strip()
    if not msg:
        return False

    # 1) 지시/참조 표현이 있으면 토픽 유지 가능성 ↑
    if _REFERENTIAL_PAT.search(msg):
        return True

    # 2) topic이 문자열이고, 메시지에 직접 등장하면 유지
    t = topic_new_val
    if isinstance(t, str):
        t = t.strip()
        if t:
            # 단순 포함 매칭(너무 빡세게 하면 오탐 줄지만 미탐 늘어남)
            if t in msg:
                return True

    # 3) (선택) 메시지에 '무슨 뜻/뭐야/설명' 패턴이 있고, topic이 들어왔으면 유지
    #    -> 이건 LLM 오염을 살릴 수도 있어서 기본은 OFF.
    # explain_like = bool(re.search(r"(뭐야|무슨 뜻|뜻이 뭐|설명|알려줘)", msg))
    # if explain_like and _has_nonempty(t):
    #     return True

    return False


def apply_session_rules(
    state: Optional[Dict[str, Any]],
    nlu: Optional[Dict[str, Any]],
    user_message: str,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    목적:
    - 세션 누적 슬롯/정책 보정(특히 education topic carry 정책)
    - 핵심 변경점:
      1) education에서 followup == False일 때는 prev_topic을 끊는다.
      2) 또한 followup == False일 때, NLU가 새 topic을 넣어도 '메시지 근거성'이 없으면 topic을 제거한다.
      3) merge 전략을 context/preference로 분리해서 실수 여지를 줄인다.
    """
    st = _safe_dict(state)
    n = _safe_dict(nlu)

    domain = _safe_str(n.get("domain") or st.get("current_domain"))
    intent = _safe_str(n.get("intent") or st.get("active_intent"))

    slots_in = _safe_dict(n.get("slots"))
    prev_slots = _safe_dict(st.get("slots"))

    # ----------------------------
    # 도메인별 기본 처리
    # ----------------------------
    if domain != "education":
        # 기존 로직: prev + in (in 우선)
        merged_slots = _merge_dict(prev_slots, slots_in)
        out = dict(n)
        out["domain"] = domain
        out["intent"] = intent
        out["slots"] = merged_slots
        return out

    # ----------------------------
    # education: followup 판정
    # ----------------------------
    follow, meta = is_followup(user_message=user_message, state=st, trace_id=trace_id)

    # education merge 전략:
    # - preference 키는 prev 유지 + in으로 업데이트 (in 우선)
    # - context 키는 followup 여부에 따라 별도 처리
    merged_slots: Dict[str, Any] = {}

    # 1) preference merge
    prev_pref = {k: v for k, v in prev_slots.items() if k in EDU_PREFERENCE_KEYS}
    in_pref = {k: v for k, v in slots_in.items() if k in EDU_PREFERENCE_KEYS}
    merged_slots = _merge_dict(prev_pref, in_pref)

    # 2) non-edu-special keys (question 같은 것들) 은 "이번 턴 우선"으로 합쳐줌
    #    (topic은 아래에서 별도 처리)
    special_keys = EDU_PREFERENCE_KEYS.union(EDU_CONTEXT_KEYS)
    prev_other = {k: v for k, v in prev_slots.items() if k not in special_keys}
    in_other = {k: v for k, v in slots_in.items() if k not in special_keys}
    merged_slots = _merge_dict(_merge_dict(merged_slots, prev_other), in_other)

    # 3) context(topic) 처리
    topic_slot_new = slots_in.get("topic")
    topic_new_val = _slot_value(topic_slot_new)
    topic_new_conf = _slot_conf(topic_slot_new)

    topic_slot_prev = prev_slots.get("topic")
    topic_prev_val = _slot_value(topic_slot_prev)

    policy_action = ""

    if not follow:
        # followup이 아니면 기본적으로 topic을 끊는다.
        # 단, NLU가 새 topic을 뽑았더라도 "메시지 근거성" 없으면 제거한다.
        keep_new = False
        if "topic" in slots_in and _has_nonempty(topic_new_val):
            keep_new = _should_keep_new_topic_when_not_followup(
                user_message=user_message,
                topic_new_val=topic_new_val,
                slots_in=slots_in,
            )

        if keep_new:
            merged_slots["topic"] = topic_slot_new
            policy_action = "cut_prev_keep_new_grounded"
        else:
            merged_slots.pop("topic", None)
            policy_action = "cut_context_drop_topic"
    else:
        # followup이면 topic 보완:
        # - 새 topic이 없거나 신뢰 낮으면 이전 topic 유지
        # - 새 topic이 충분하면 새 topic으로 업데이트
        if (not _has_nonempty(topic_new_val)) or (topic_new_conf < 0.35):
            if _has_nonempty(topic_prev_val):
                merged_slots["topic"] = topic_slot_prev
                policy_action = "carry_context_use_prev"
            else:
                merged_slots.pop("topic", None)
                policy_action = "carry_context_no_topic"
        else:
            merged_slots["topic"] = topic_slot_new
            policy_action = "carry_context_use_new"

    # ----------------------------
    # 디버그/로그
    # ----------------------------
    log_event(trace_id, "edu_context_policy", {
        "domain": domain,
        "intent": intent,
        "followup": follow,
        "followup_meta": meta,
        "policy_action": policy_action,
        "topic_prev": topic_prev_val,
        "topic_new": topic_new_val,
        "topic_new_conf": topic_new_conf,
        "slots_in_keys": list(slots_in.keys()),
        "slots_out_keys": list(merged_slots.keys()),
        "last_bot_action": _last_bot_action(st),
    })

    out = dict(n)
    out["domain"] = domain
    out["intent"] = intent
    out["slots"] = merged_slots
    return out
