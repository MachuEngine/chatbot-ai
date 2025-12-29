# nlu/normalizer.py
from __future__ import annotations

import re
import time
from typing import Any, Dict, Optional

try:
    from utils.logging import log_event
except Exception:
    log_event = None

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
        # 슬롯 값이 딕셔너리인데 비어있거나 value가 None인 경우도 체크할 수 있으나
        # 여기서는 단순 병합
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


def _looks_like_new_order(msg: str) -> bool:
    """
    pending 옵션 질문 중에 사용자가 새 주문을 말한 것으로 보이면
    pending followup을 해제해서 "새 주문"으로 흘려보내기 위한 휴리스틱.
    """
    m = (msg or "").strip()
    if not m:
        return False

    triggers = [
        "주세요",
        "주문",
        "시킬게",
        "시켜",
        "할게요",
        "할게",
        "다시",
        "추가",
        "하나",
        "두",
        "세",
        "한잔",
        "한 잔",
        "두잔",
        "두 잔",
        "세잔",
        "세 잔",
    ]
    return any(t in m for t in triggers)


# ----------------------------
# education 정책 키들 (Sticky Context 적용)
# ----------------------------

# ✅ Sticky Keys: 명시적으로 바꾸지 않는 한 대화 내내 유지됨
STICKY_CONTEXT_KEYS = {
    "level",
    "subject",
    "style",
    "tone",
    "language",
    "include_examples",
    "target_improvements",
}

# ✅ Topic Keys: 주제가 전환되면 사라짐 (Follow-up일 때만 유지)
TOPIC_CONTEXT_KEYS = {
    "topic",
    "content",
    "student_answer",
    "question",
    "context",
    "rubric",
}


def _normalize_korean_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[\"'“”‘’.,!?()\[\]{}<>:;~`/\\|@#$%^&*_+=-]", "", s)
    return s


# ----------------------------
# kiosk option_groups helpers
# ----------------------------

def _option_groups_to_dict(v: Any) -> Dict[str, Any]:
    if v is None:
        return {}

    # slot wrapper
    if isinstance(v, dict) and "value" in v:
        inner = v.get("value")
        if isinstance(inner, dict):
            return dict(inner)
        if isinstance(inner, list):
            out: Dict[str, Any] = {}
            for it in inner:
                if isinstance(it, dict) and isinstance(it.get("group"), str):
                    out[it["group"].strip()] = it.get("value")
            return out
        return {}

    # plain dict mapping
    if isinstance(v, dict):
        return dict(v)

    # list form
    if isinstance(v, list):
        out: Dict[str, Any] = {}
        for it in v:
            if isinstance(it, dict) and isinstance(it.get("group"), str):
                out[it["group"].strip()] = it.get("value")
        return out

    return {}


def _wrap_option_groups(og: Dict[str, Any], conf: float = 0.9) -> Dict[str, Any]:
    return {"value": dict(og or {}), "confidence": float(conf)}


def _choice_match(value: Any, choices: Any) -> Optional[str]:
    if not isinstance(choices, list):
        return None
    if value is None:
        return None

    v = str(value).strip()
    if not v:
        return None

    v_norm = re.sub(r"\s+", "", v).lower()

    for c in choices:
        if not isinstance(c, str):
            continue
        c_norm = re.sub(r"\s+", "", c).lower()
        if c_norm == v_norm:
            return c
    return None


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

    # ----------------------------
    # 1. Kiosk 및 기타(education 제외) - 기존 로직 유지
    # ----------------------------
    if domain != "education":
        pending_group = _safe_str(st.get("pending_option_group")).strip()
        pending_choices = st.get("pending_option_group_choices")
        last_action = _last_bot_action(st)

        is_pending_followup = bool(pending_group) and (last_action == "ask_option_group")

        if is_pending_followup:
            active = _safe_str(st.get("active_intent")).strip()
            if active:
                intent = active

            msg = (user_message or "").strip()

            def _norm_temp(s: str) -> Optional[str]:
                s2 = (s or "").strip().lower()
                if not s2:
                    return None
                s2n = re.sub(r"[^a-z0-9가-힣]", "", s2)
                if any(k in s2n for k in ["아이스", "ice", "iced", "차가", "시원", "콜드", "cold"]):
                    return "ice"
                if any(k in s2n for k in ["뜨거", "따뜻", "따듯", "핫", "hot"]):
                    return "hot"
                return None

            def _norm_size(s: str) -> Optional[str]:
                s2 = (s or "").strip().lower()
                if not s2:
                    return None
                s2n = re.sub(r"[^a-z0-9가-힣]", "", s2)
                m = re.match(r"^(s|m|l)", s2n)
                if m:
                    return m.group(1).upper()
                if "small" in s2n: return "S"
                if "medium" in s2n: return "M"
                if "large" in s2n: return "L"
                if any(k in s2n for k in ["제일작", "가장작", "작은", "스몰"]): return "S"
                if any(k in s2n for k in ["중간", "보통", "미디움"]): return "M"
                if any(k in s2n for k in ["제일큰", "가장큰", "큰", "라지"]): return "L"
                return None

            temp_candidate = _norm_temp(msg)
            size_candidate = _norm_size(msg)

            coerced: Optional[str] = None
            extra_updates: Dict[str, Any] = {}

            if pending_group == "temperature":
                coerced = temp_candidate
                if size_candidate is not None:
                    extra_updates["size"] = size_candidate
            elif pending_group == "size":
                coerced = size_candidate
                if temp_candidate is not None:
                    extra_updates["temperature"] = temp_candidate

            if coerced is None and not extra_updates:
                og_from_llm = _option_groups_to_dict(slots_in.get("option_groups"))
                if pending_group and pending_group in og_from_llm:
                    cand = og_from_llm.get(pending_group)
                    matched = _choice_match(cand, pending_choices)
                    if matched is not None:
                        coerced = matched
                    elif cand is not None and str(cand).strip() != "":
                        coerced = str(cand).strip()

            if coerced is None and isinstance(pending_choices, list):
                msg_l = re.sub(r"\s+", "", msg).lower()
                for c in pending_choices:
                    if not isinstance(c, str):
                        continue
                    if re.sub(r"\s+", "", c).lower() == msg_l:
                        coerced = c
                        break

            if coerced is None and not extra_updates:
                if "option_groups" in slots_in:
                    slots_in.pop("option_groups", None)

            if coerced is None and (not extra_updates) and _looks_like_new_order(msg):
                is_pending_followup = False

            if coerced is not None or extra_updates:
                slots_in.pop("item_name", None)
                prev_og = _option_groups_to_dict(prev_slots.get("option_groups"))
                cur_og = _option_groups_to_dict(slots_in.get("option_groups"))
                og_dict: Dict[str, Any] = dict(prev_og)
                og_dict.update(cur_og)

                if coerced is not None and pending_group:
                    og_dict[pending_group] = coerced
                for k, v in extra_updates.items():
                    og_dict[k] = v

                slots_in["option_groups"] = _wrap_option_groups(og_dict, conf=0.9)
                if "quantity" not in slots_in and "quantity" in prev_slots:
                    slots_in["quantity"] = prev_slots.get("quantity")

        if is_pending_followup:
            merged_slots = _merge_dict(prev_slots, slots_in)
        else:
            merged_slots = dict(slots_in)

        out = dict(n)
        out["domain"] = domain
        out["intent"] = intent
        out["slots"] = merged_slots
        return out

    # ----------------------------
    # 2. Education Logic (Sticky Context 적용)
    # ----------------------------
    is_f, meta = is_followup(user_message, st, trace_id)
    
    merged_slots = {}
    
    # (1) Sticky Keys Logic
    # 이 키들은 새로운 값이 들어오지 않는 한 무조건 이전 턴의 값을 유지
    prev_sticky = {k: v for k, v in prev_slots.items() if k in STICKY_CONTEXT_KEYS}
    new_sticky = {k: v for k, v in slots_in.items() if k in STICKY_CONTEXT_KEYS}
    
    # 새 값이 있으면 덮어쓰고, 없으면 이전 값 유지
    merged_slots.update(_merge_dict(prev_sticky, new_sticky))
    
    # (2) Topic Context Logic
    # Follow-up(꼬리질문)인 경우에만 이전 토픽/콘텐츠를 유지
    prev_topic = {k: v for k, v in prev_slots.items() if k in TOPIC_CONTEXT_KEYS}
    new_topic = {k: v for k, v in slots_in.items() if k in TOPIC_CONTEXT_KEYS}
    
    policy_action = ""
    
    if is_f:
        # Follow-up이면 문맥 병합 (예: "그거 예문 줘" -> "그거"=이전 topic)
        merged_slots.update(_merge_dict(prev_topic, new_topic))
        policy_action = "followup_merge_context"
    else:
        # Follow-up이 아니면 이전 토픽 폐기, 새 토픽만 사용
        merged_slots.update(new_topic)
        policy_action = "new_topic_reset_context"
        
    # (3) Other keys (그 외 슬롯들은 이번 턴의 것만 사용)
    other_keys = set(slots_in.keys()) - STICKY_CONTEXT_KEYS - TOPIC_CONTEXT_KEYS
    for k in other_keys:
        merged_slots[k] = slots_in[k]

    if log_event and trace_id:
        log_event(trace_id, "edu_context_policy", {
            "domain": domain,
            "intent": intent,
            "is_followup": is_f,
            "action": policy_action,
            "sticky_keys_kept": list(merged_slots.keys()),
            "last_bot_action": _last_bot_action(st),
        })

    out = dict(n)
    out["domain"] = domain
    out["intent"] = intent
    out["slots"] = merged_slots
    return out