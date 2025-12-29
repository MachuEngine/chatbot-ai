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


# ----------------------------
# kiosk option_groups helpers
# ----------------------------

def _option_groups_to_dict(v: Any) -> Dict[str, Any]:
    """
    option_groups 슬롯/값을 dict로 정규화.
    허용 형태:
      - {"value": {...}}
      - {...}
      - [{"group":"size","value":"S"}, ...]
      - None
    """
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
    """
    pending_choices 안에서 value를 최대한 맞춰서 반환.
    - 공백/대소문자 무시
    - "따듯" 같은 표현은 여기서 해결하지 않고, value 자체가 'hot/ice/S/M/L'로 들어온 케이스를 살린다.
    """
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
    # kiosk 및 기타(education 제외)
    # ----------------------------
    if domain != "education":
        pending_group = _safe_str(st.get("pending_option_group")).strip()
        pending_choices = st.get("pending_option_group_choices")
        last_action = _last_bot_action(st)

        is_pending_followup = bool(pending_group) and (last_action == "ask_option_group")

        # ✅ pending 옵션 응답은 직전 의도를 유지하도록 intent 고정
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
                # ✅ 따듯/따뜻 모두 대응
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

                if "small" in s2n:
                    return "S"
                if "medium" in s2n:
                    return "M"
                if "large" in s2n:
                    return "L"

                if any(k in s2n for k in ["제일작", "가장작", "작은", "스몰"]):
                    return "S"
                if any(k in s2n for k in ["중간", "보통", "미디움"]):
                    return "M"
                if any(k in s2n for k in ["제일큰", "가장큰", "큰", "라지"]):
                    return "L"

                if s2n == "tall":
                    return "Tall"
                if s2n == "grande":
                    return "Grande"
                if s2n == "venti":
                    return "Venti"

                return None

            # ✅ pending 상황에서도 "명시된 다른 옵션"을 같이 반영하기 위해 둘 다 검사
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

            # ----------------------------
            # ✅ FIX: 휴리스틱 실패 시에도 LLM option_groups 후보를 살린다
            # ----------------------------
            if coerced is None and not extra_updates:
                og_from_llm = _option_groups_to_dict(slots_in.get("option_groups"))
                if pending_group and pending_group in og_from_llm:
                    cand = og_from_llm.get(pending_group)
                    matched = _choice_match(cand, pending_choices)
                    # choices가 없으면 cand를 그대로 쓰되, 최소한 non-empty면 채택
                    if matched is not None:
                        coerced = matched
                        log_event(
                            trace_id,
                            "kiosk_pending_take_llm_option_group",
                            {"pending_group": pending_group, "picked": coerced, "via": "choices_match"},
                        )
                    elif cand is not None and str(cand).strip() != "":
                        coerced = str(cand).strip()
                        log_event(
                            trace_id,
                            "kiosk_pending_take_llm_option_group",
                            {"pending_group": pending_group, "picked": coerced, "via": "raw"},
                        )

            # choices가 있으면 그 안에서 최대한 맞추기(대소문자/공백 무시) - user_message 직접 매칭
            if coerced is None and isinstance(pending_choices, list):
                msg_l = re.sub(r"\s+", "", msg).lower()
                for c in pending_choices:
                    if not isinstance(c, str):
                        continue
                    if re.sub(r"\s+", "", c).lower() == msg_l:
                        coerced = c
                        break

            # ✅ (중요) coercion 실패이면서, extra_updates도 없을 때만
            # LLM이 option_groups: []/{} 같은 "빈 값"을 보내더라도 prev option_groups를 덮어쓰지 않게 drop
            if coerced is None and not extra_updates:
                if "option_groups" in slots_in:
                    slots_in.pop("option_groups", None)
                    log_event(
                        trace_id,
                        "kiosk_pending_drop_empty_option_groups",
                        {
                            "pending_group": pending_group,
                            "user_message": msg[:120],
                            "reason": "coercion_failed_keep_prev",
                        },
                    )

            # ✅ 옵션 값으로 coercion 실패 + extra도 없음 + 새 주문처럼 보이면 pending followup 해제
            if coerced is None and (not extra_updates) and _looks_like_new_order(msg):
                is_pending_followup = False

            # ✅ 실제 반영: pending 값(coerced) 또는 extra_updates 중 하나라도 있으면 option_groups merge
            if coerced is not None or extra_updates:
                # NLU가 옵션 답변을 item_name으로 오염시키는 케이스 방지
                slots_in.pop("item_name", None)

                # ✅ prev option_groups + 이번 option_groups를 merge해서 유지
                prev_og = _option_groups_to_dict(prev_slots.get("option_groups"))
                cur_og = _option_groups_to_dict(slots_in.get("option_groups"))

                og_dict: Dict[str, Any] = dict(prev_og)
                og_dict.update(cur_og)  # 이번 턴에서 나온 값이 있으면 덮어씀

                # pending_group 값
                if coerced is not None and pending_group:
                    og_dict[pending_group] = coerced

                # 추가 업데이트(명시된 다른 옵션)
                for k, v in extra_updates.items():
                    og_dict[k] = v

                slots_in["option_groups"] = _wrap_option_groups(og_dict, conf=0.9)

                # (선택) quantity가 비어있으면 prev를 유지
                if "quantity" not in slots_in and "quantity" in prev_slots:
                    slots_in["quantity"] = prev_slots.get("quantity")

        # ✅ 슬롯 오염 방지:
        # kiosk(education 제외)는 "pending followup"일 때만 prev_slots carry
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
    # education: 기존 로직 그대로
    # ----------------------------
    follow, meta = is_followup(user_message=user_message, state=st, trace_id=trace_id)

    merged_slots: Dict[str, Any] = {}

    prev_pref = {k: v for k, v in prev_slots.items() if k in EDU_PREFERENCE_KEYS}
    in_pref = {k: v for k, v in slots_in.items() if k in EDU_PREFERENCE_KEYS}
    merged_slots = _merge_dict(prev_pref, in_pref)

    special_keys = EDU_PREFERENCE_KEYS.union(EDU_CONTEXT_KEYS)
    prev_other = {k: v for k, v in prev_slots.items() if k not in special_keys}
    in_other = {k: v for k, v in slots_in.items() if k not in special_keys}
    merged_slots = _merge_dict(_merge_dict(merged_slots, prev_other), in_other)

    topic_slot_new = slots_in.get("topic")
    topic_new_val = _slot_value(topic_slot_new)
    topic_new_conf = _slot_conf(topic_slot_new)

    topic_slot_prev = prev_slots.get("topic")
    topic_prev_val = _slot_value(topic_slot_prev)

    policy_action = ""

    if not follow:
        keep_new_topic = False
        if "topic" in slots_in and _has_nonempty(topic_new_val):
            keep_new_topic = _should_keep_new_topic_when_not_followup(
                user_message=user_message,
                topic_new_val=topic_new_val,
            )

        for k in EDU_CONTEXT_KEYS:
            merged_slots.pop(k, None)

        if keep_new_topic:
            merged_slots["topic"] = topic_slot_new
            policy_action = "cut_prev_keep_new_grounded"
        else:
            policy_action = "cut_context_drop_topic"

    else:
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
