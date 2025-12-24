# nlu/validator.py
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple, List

from utils.logging import log_event
from nlu.messages import TEMPLATES

try:
    from domain.kiosk.schema import KIOSK_SCHEMA  # type: ignore
except Exception:
    KIOSK_SCHEMA = {}

try:
    from domain.education.schema import EDUCATION_SCHEMA  # type: ignore
except Exception:
    EDUCATION_SCHEMA = {}

SCHEMAS: Dict[str, Dict[str, Any]] = {
    "kiosk": KIOSK_SCHEMA or {},
    "education": EDUCATION_SCHEMA or {},
}

# kiosk 결과 템플릿 키(OK)
RESULT_KEYS_OK: Dict[Tuple[str, str], str] = {
    ("kiosk", "add_item"): "result.kiosk.add_item",
    ("kiosk", "modify_item"): "result.kiosk.modify_item",
    ("kiosk", "remove_item"): "result.kiosk.remove_item",
    ("kiosk", "checkout"): "result.kiosk.checkout",
    ("kiosk", "cancel_order"): "result.kiosk.cancel_order",
    ("kiosk", "refund_order"): "result.kiosk.refund_order",
    ("kiosk", "ask_store_info"): "result.kiosk.ask_store_info",
}

# 실패 템플릿 키
RESULT_KEYS_FAIL: Dict[Tuple[str, str], str] = {
    ("kiosk", "checkout"): "result.fail.kiosk.checkout",
    ("kiosk", "cancel_order"): "result.fail.kiosk.cancel_order",
    ("kiosk", "refund_order"): "result.fail.kiosk.refund_order",
}

REQUIRED_OPTION_GROUPS: Dict[Tuple[str, str], List[str]] = {
    ("kiosk", "add_item"): ["temperature"],
}

# ✅ education 인텐트는 전부 생성형으로 처리(LLM task 반드시 부착)
EDU_LLM_INTENTS = {
    "ask_question",
    "explain_concept",
    "summarize_text",
    "give_feedback",
    "create_practice",
    "check_answer",
    "rewrite",
}


def _now_ts() -> float:
    return time.time()


def _safe_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _safe_str(x: Any) -> str:
    return x if isinstance(x, str) else "" if x is None else str(x)


def _merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """
    b가 우선.
    b의 값이 None이면 skip (기존값 유지)
    """
    out = dict(a or {})
    for k, v in (b or {}).items():
        if v is None:
            continue
        out[k] = v
    return out


def _unwrap_slot_value(v: Any) -> Any:
    # NLU가 {"value":..., "confidence":...} 형태로 줄 때 value만 꺼냄
    if isinstance(v, dict) and "value" in v:
        return v.get("value")
    return v


def _unwrap_slots(slots: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in (slots or {}).items():
        val = _unwrap_slot_value(v)
        if val is None:
            continue
        out[k] = val
    return out


def _is_nonempty(x: Any) -> bool:
    """
    중요: slot이 {"value": None, "confidence": 0.0} 인 경우를 "비어있음"으로 봐야 함.
    기존 구현은 dict면 len>0이라 True가 나와서 required 체크가 무력화될 수 있었음.
    """
    x = _unwrap_slot_value(x)

    if x is None:
        return False
    if isinstance(x, str):
        return bool(x.strip())
    if isinstance(x, (list, dict, tuple, set)):
        return len(x) > 0
    return True


def _intent_schema(schema: Dict[str, Any], intent: str) -> Dict[str, Any]:
    intents = schema.get("intents") or {}
    return intents.get(intent) or {}


def _required_slots(schema: Dict[str, Any], intent: str) -> List[str]:
    i = _intent_schema(schema, intent)
    return list(i.get("required_slots") or [])


def _missing_required(required: List[str], slots: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    for k in required:
        if not _is_nonempty(slots.get(k)):
            missing.append(k)
    return missing


def _format_template(key: str, vars: Dict[str, Any]) -> str:
    tmpl = TEMPLATES.get(key) or TEMPLATES.get("fallback.mvp") or ""
    try:
        return tmpl.format(**(vars or {}))
    except Exception:
        return tmpl


def _ask_slot_key(slot_name: str) -> str:
    known = {
        "item_name", "quantity", "target_item_ref", "order_ref", "info_type",
        "question", "content", "student_answer"
    }
    return f"ask.slot.{slot_name}" if slot_name in known else "ask.slot.generic"


def _ask_option_key(group: str) -> str:
    known = {"temperature", "size"}
    return f"ask.option_group.{group}" if group in known else "ask.option_group.generic"


def _extract_user_message(req: Any) -> str:
    """
    req 형태가 프로젝트마다 달라질 수 있어서 방어적으로 추출.
    """
    if isinstance(req, dict):
        return _safe_str(req.get("user_message"))
    # pydantic / dataclass 류
    return _safe_str(getattr(req, "user_message", ""))


def validate_and_build_action(
    req: Any,
    state: Optional[Dict[str, Any]],
    nlu: Optional[Dict[str, Any]],
    trace_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    반환:
      action: {"reply": {...}, "plan": {...}}
      new_state: dict
    """
    st = _safe_dict(state)
    nlu = _safe_dict(nlu)

    domain = _safe_str(nlu.get("domain") or st.get("current_domain") or "").strip()
    intent = _safe_str(nlu.get("intent") or st.get("active_intent") or "").strip()

    # ✅ 핵심 수정:
    # normalizer에서 이미 "최종 슬롯"을 만들어서 내려준다.
    # 여기서 prev_slots와 다시 merge하면 삭제(drop)가 표현되지 않아 topic 등이 resurrect(부활)한다.
    slots_in = _safe_dict(nlu.get("slots"))
    prev_slots = _safe_dict(st.get("slots"))

    # nlu slots가 있으면 그걸 "진실"로 사용 (replace semantics)
    # 없다면(예외) prev_slots 사용
    slots = dict(slots_in) if slots_in else dict(prev_slots)

    # ✅ 추가 보정(education):
    # NLU가 question을 이전 값으로 재사용하는 경우가 있었으니,
    # education에서 question 계열 intent는 user_message를 우선으로 강제.
    user_message = _extract_user_message(req)
    if domain == "education" and intent in {"ask_question", "explain_concept"}:
        if user_message:
            slots["question"] = {"value": user_message, "confidence": 1.0}

    schema = SCHEMAS.get(domain) or {}
    if not schema:
        reply = {
            "text": _format_template("fallback.mvp", {}),
            "action_type": "fallback",
        }
        new_state = _merge(st, {
            "current_domain": domain or st.get("current_domain"),
            "active_intent": intent or st.get("active_intent"),
            "slots": slots,
            "last_bot_action": "fallback",
            "turn_index": int(st.get("turn_index") or 0) + 1,
            "updated_at": _now_ts(),
            "debug_last_reason": "fallback:no_schema",
        })
        return {"reply": reply}, new_state

    # 1) required slots 체크
    required = _required_slots(schema, intent)
    missing = _missing_required(required, slots)
    if missing:
        slot_name = missing[0]
        key = _ask_slot_key(slot_name)
        text = _format_template(key, {"slot": slot_name})
        reply = {
            "text": text,
            "action_type": "ask_slot",
            "ui_hints": {"expect_slot": slot_name},
        }
        new_state = _merge(st, {
            "current_domain": domain,
            "active_intent": intent,
            "slots": slots,
            "last_bot_action": "ask_slot",
            "turn_index": int(st.get("turn_index") or 0) + 1,
            "updated_at": _now_ts(),
            "debug_last_reason": f"missing_required:{slot_name}",
        })
        log_event(trace_id, "validator_missing_required", {"domain": domain, "intent": intent, "missing": missing})
        return {"reply": reply}, new_state

    # 2) required option groups 체크 (kiosk)
    req_ogs = REQUIRED_OPTION_GROUPS.get((domain, intent)) or []
    if req_ogs:
        og = slots.get("option_groups")
        og_val = _unwrap_slot_value(og)
        og_dict = og_val if isinstance(og_val, dict) else {}
        for g in req_ogs:
            if not _is_nonempty(og_dict.get(g)):
                key = _ask_option_key(g)
                text = _format_template(key, {"group": g})
                reply = {
                    "text": text,
                    "action_type": "ask_option_group",
                    "ui_hints": {"expect_option_group": g},
                }
                new_state = _merge(st, {
                    "current_domain": domain,
                    "active_intent": intent,
                    "slots": slots,
                    "last_bot_action": "ask_option_group",
                    "turn_index": int(st.get("turn_index") or 0) + 1,
                    "updated_at": _now_ts(),
                    "debug_last_reason": f"missing_option_group:{g}",
                })
                log_event(trace_id, "validator_missing_option_group", {"domain": domain, "intent": intent, "expect": g})
                return {"reply": reply}, new_state

    # plan(실행용)
    plan = {"domain": domain, "intent": intent, "slots": slots}

    # message keys
    message_key_ok = RESULT_KEYS_OK.get((domain, intent), "fallback.mvp")
    message_key_fail = RESULT_KEYS_FAIL.get((domain, intent), "result.fail.generic")

    reply: Dict[str, Any] = {
        "text": "",
        "action_type": "answer",
        "ui_hints": {"domain": domain, "intent": intent},
        "message_key_ok": message_key_ok,
        "message_key_fail": message_key_fail,
    }

    # ✅ education은 LLM task를 반드시 붙여서 executor/renderer가 생성하도록 함
    if domain == "education" and intent in EDU_LLM_INTENTS:
        # slots는 unwrap해서 넘김
        reply["llm_task"] = {
            "kind": f"edu_{intent}",
            "slots": _unwrap_slots(slots),
        }

    action = {"reply": reply, "plan": plan}

    new_state = _merge(st, {
        "current_domain": domain,
        "active_intent": intent,
        "slots": slots,  # ✅ replace semantics 유지
        "last_bot_action": "answer",
        "turn_index": int(st.get("turn_index") or 0) + 1,
        "updated_at": _now_ts(),
        "debug_last_reason": "action:planned",
    })

    log_event(trace_id, "validator_planned", {
        "domain": domain,
        "intent": intent,
        "ok_key": message_key_ok,
        "fail_key": message_key_fail,
        "has_llm_task": bool(reply.get("llm_task")),
        "llm_kind": (reply.get("llm_task") or {}).get("kind") if isinstance(reply.get("llm_task"), dict) else None,
    })

    return action, new_state
