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

EDU_PREFERENCE_KEYS = {
    "level",
    "subject",
    "style",
    "include_examples",
    "example_type",
    "language",
    "length",
    "tone",
    "target_improvements",
}

# ✅ 중요: education에서 "일회성/컨텍스트성" 슬롯들
# followup이 아니면 끊어서 오염 방지
EDU_CONTEXT_KEYS = {
    "topic",
    "content",
    "student_answer",
    "question",
}

_TOPIC_GROUNDED_MINLEN = 2


def _normalize_korean_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[\"'“”‘’.,!?()\[\]{}<>:;~`/\\|@#$%^&*_+=-]", "", s)
    return s


def _extract_topic_keywords(topic: str) -> list[str]:
    t = (topic or "").strip()
    if not t:
        return []

    t = re.sub(r"(의|과|와|및|또는|그리고)", " ", t)
    parts = re.split(r"\s+", t)
    kws = []
    for p in parts:
        p = p.strip()
        if len(p) >= _TOPIC_GROUNDED_MINLEN:
            kws.append(p)

    seen = set()
    out = []
    for k in kws:
        if k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def _should_keep_new_topic_when_not_followup(user_message: str, topic_new_val: Any) -> bool:
    msg = (user_message or "").strip()
    if not msg:
        return False

    t = topic_new_val
    if not isinstance(t, str):
        return False
    t = t.strip()
    if len(t) < _TOPIC_GROUNDED_MINLEN:
        return False

    if t in msg:
        return True

    msg_norm = _normalize_korean_text(msg)
    t_norm = _normalize_korean_text(t)
    if t_norm and t_norm in msg_norm:
        return True

    for kw in _extract_topic_keywords(t):
        kw_norm = _normalize_korean_text(kw)
        if kw_norm and kw_norm in msg_norm:
            return True

    return False


def apply_session_rules(
    state: Optional[Dict[str, Any]],
    nlu: Optional[Dict[str, Any]],
    user_message: str,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    st = _safe_dict(state)
    n = _safe_dict(nlu)

    domain = _safe_str(n.get("domain") or st.get("current_domain")).strip()
    intent = _safe_str(n.get("intent") or st.get("active_intent")).strip()

    slots_in = _safe_dict(n.get("slots"))
    prev_slots = _safe_dict(st.get("slots"))

    # education 이외: 기존 방식 유지
    if domain != "education":
        merged_slots = _merge_dict(prev_slots, slots_in)
        out = dict(n)
        out["domain"] = domain
        out["intent"] = intent
        out["slots"] = merged_slots
        return out

    # education: followup 판정
    follow, meta = is_followup(user_message=user_message, state=st, trace_id=trace_id)

    merged_slots: Dict[str, Any] = {}

    # 1) preference merge
    prev_pref = {k: v for k, v in prev_slots.items() if k in EDU_PREFERENCE_KEYS}
    in_pref = {k: v for k, v in slots_in.items() if k in EDU_PREFERENCE_KEYS}
    merged_slots = _merge_dict(prev_pref, in_pref)

    # 2) other merge (context 제외)
    special_keys = EDU_PREFERENCE_KEYS.union(EDU_CONTEXT_KEYS)
    prev_other = {k: v for k, v in prev_slots.items() if k not in special_keys}
    in_other = {k: v for k, v in slots_in.items() if k not in special_keys}
    merged_slots = _merge_dict(_merge_dict(merged_slots, prev_other), in_other)

    # 3) context(topic 등) 처리
    topic_slot_new = slots_in.get("topic")
    topic_new_val = _slot_value(topic_slot_new)
    topic_new_conf = _slot_conf(topic_slot_new)

    topic_slot_prev = prev_slots.get("topic")
    topic_prev_val = _slot_value(topic_slot_prev)

    policy_action = ""

    if not follow:
        # ✅ followup이 아니면 일회성/컨텍스트 슬롯은 기본적으로 끊는다.
        # topic만 "메시지 근거"가 있으면 유지 가능.
        keep_new_topic = False
        if "topic" in slots_in and _has_nonempty(topic_new_val):
            keep_new_topic = _should_keep_new_topic_when_not_followup(
                user_message=user_message,
                topic_new_val=topic_new_val,
            )

        # 컨텍스트 슬롯 드랍
        for k in EDU_CONTEXT_KEYS:
            merged_slots.pop(k, None)

        if keep_new_topic:
            merged_slots["topic"] = topic_slot_new
            policy_action = "cut_prev_keep_new_grounded"
        else:
            policy_action = "cut_context_drop_topic"

    else:
        # followup이면 topic만 carry 정책 적용(나머지 content/student_answer/question은
        # followup이면 슬롯으로 다시 들어오는 게 자연스러우므로 slots_in에 맡김)
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
