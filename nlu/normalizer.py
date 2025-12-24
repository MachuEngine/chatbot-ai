# nlu/normalizer.py
from __future__ import annotations

import re
import time
from typing import Any, Dict, Optional

from utils.logging import log_event
from nlu.followup import is_followup


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
    if (
        isinstance(slot, dict)
        and "confidence" in slot
        and isinstance(slot.get("confidence"), (int, float))
    ):
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


def _last_bot_action(state: Optional[Dict[str, Any]]) -> str:
    if isinstance(state, dict):
        return _safe_str(state.get("last_bot_action"))
    return ""


# ----------------------------
# education 정책 키들
# ----------------------------

# education에서 "유지해도 되는 사용자 선호" 슬롯들
EDU_PREFERENCE_KEYS = {
    "level",
    "subject",
    "style",
    "include_examples",
    "example_type",
    "language",
    "length",
    "tone",
    # 요구/선호 성격이라 유지하는 편이 안전
    "target_improvements",
}

# education에서 "컨텍스트성" 슬롯들: followup이 아니면 끊는 대상
EDU_CONTEXT_KEYS = {
    "topic",
}

# (선택) 메시지에서 topic 근거성을 더 세게 보는 경우 쓸 수 있는 패턴
# 지금은 "topic 문자열이 메시지에 직접 등장"만 근거로 인정(오염 방지 우선).
# 필요하면 여기에 규칙을 추가하되, 오탐으로 topic이 부활하지 않게 매우 보수적으로.
_TOPIC_GROUNDED_MINLEN = 2  # 토픽이 너무 짧으면 근거 판단이 불안정하니 최소 길이


def _should_keep_new_topic_when_not_followup(user_message: str, topic_new_val: Any) -> bool:
    """
    followup == False 인 상황에서,
    NLU가 topic을 넣었더라도 '사용자 메시지에 근거가 있을 때만' 유지.

    ✅ 오염 방지 우선:
    - 지시/참조(이거/그거)만으로는 topic을 살리지 않음
    - topic 문자열이 user_message 안에 '직접' 등장할 때만 유지
    """
    msg = (user_message or "").strip()
    if not msg:
        return False

    t = topic_new_val
    if not isinstance(t, str):
        return False
    t = t.strip()
    if len(t) < _TOPIC_GROUNDED_MINLEN:
        return False

    # 단순 포함(보수적) — 필요하면 정규화/형태소까지 가야하지만 일단 오염 방지에 유리
    return t in msg


def apply_session_rules(
    state: Optional[Dict[str, Any]],
    nlu: Optional[Dict[str, Any]],
    user_message: str,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    목적:
    - 세션 누적 슬롯/정책 보정(특히 education topic carry 정책)

    핵심:
      1) education에서 followup == False일 때는 prev_topic을 끊는다.
      2) followup == False일 때, NLU가 topic을 넣어도 '메시지 근거성'이 없으면 제거한다.
      3) education은 merge를 preference/context/other로 분리해서 실수(토픽 부활) 여지를 줄인다.
    """
    st = _safe_dict(state)
    n = _safe_dict(nlu)

    domain = _safe_str(n.get("domain") or st.get("current_domain")).strip()
    intent = _safe_str(n.get("intent") or st.get("active_intent")).strip()

    slots_in = _safe_dict(n.get("slots"))
    prev_slots = _safe_dict(st.get("slots"))

    # ----------------------------
    # education 이외: 기존 방식 유지
    # ----------------------------
    if domain != "education":
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
    # - preference 키: prev 유지 + 이번 턴 업데이트(in 우선)
    # - other 키: prev 유지 + 이번 턴 업데이트(in 우선)
    # - context(topic): 아래에서 followup 정책으로 별도 처리
    merged_slots: Dict[str, Any] = {}

    # 1) preference merge
    prev_pref = {k: v for k, v in prev_slots.items() if k in EDU_PREFERENCE_KEYS}
    in_pref = {k: v for k, v in slots_in.items() if k in EDU_PREFERENCE_KEYS}
    merged_slots = _merge_dict(prev_pref, in_pref)

    # 2) other merge (topic 제외)
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
        # 단, 이번 턴 topic이 "사용자 메시지 근거"가 있으면 그 토픽은 유지 가능.
        keep_new = False
        if "topic" in slots_in and _has_nonempty(topic_new_val):
            keep_new = _should_keep_new_topic_when_not_followup(
                user_message=user_message,
                topic_new_val=topic_new_val,
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

    log_event(
        trace_id,
        "edu_context_policy",
        {
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
        },
    )

    out = dict(n)
    out["domain"] = domain
    out["intent"] = intent
    out["slots"] = merged_slots
    return out
