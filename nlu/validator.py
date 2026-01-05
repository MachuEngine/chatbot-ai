# nlu/validator.py
from __future__ import annotations

import re
import json
from typing import Any, Dict, Optional, List, Tuple

from utils.logging import log_event
from domain.kiosk.catalog_repo import CatalogRepo
from domain.kiosk.policy import (
    get_required_option_groups_for_add_item,
    find_missing_required_option_group,
    default_catalog_repo,
)
from domain.driving.policy import build_vehicle_command, check_action_validity
from domain.driving.state_manager import VehicleStateManager

# LLM Reasoning을 위한 클라이언트 임포트
from nlu.llm_answer_client import answer_with_openai

TEMPLATES = {
    "result.kiosk.add_item": "",
    "result.fail.generic": "",
}

_KO_PART = {
    "window": "창문", "door_lock": "문", "seat_heater": "열선 시트", "seat_ventilation": "통풍 시트",
    "trunk": "트렁크", "frunk": "프렁크", "light": "조명", "wiper": "와이퍼", "mirror": "사이드 미러",
    "sunroof": "선루프", "steering_wheel": "핸들 열선", "charge_port": "충전구"
}
_KO_ACTION = {
    "open": "엽니다", "close": "닫습니다", "on": "켭니다", "off": "끕니다",
    "lock": "잠급니다", "unlock": "엽니다", "up": "올립니다", "down": "내립니다",
    "fold": "접습니다", "unfold": "폅니다", "tilt": "기울입니다"
}


def _slot_value(slots: Dict[str, Any], key: str) -> Any:
    v = slots.get(key)
    if isinstance(v, dict):
        return v.get("value")
    return v


def _merge_state(state: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    new_state = dict(state or {})
    new_state.update(patch or {})
    new_state["turn_index"] = int(new_state.get("turn_index", 0)) + 1
    return new_state


def _normalize_option_groups(option_groups: Any) -> Dict[str, Any]:
    if option_groups is None: return {}
    if isinstance(option_groups, dict):
        v = option_groups.get("value")
        if isinstance(v, dict): return v
        return option_groups
    if isinstance(option_groups, list):
        out: Dict[str, Any] = {}
        for it in option_groups:
            if not isinstance(it, dict): continue
            g = it.get("group")
            v = it.get("value")
            if isinstance(g, str) and g.strip():
                out[g.strip()] = v
        return out
    return {}


def _normalize_temperature_value(v: Any) -> Any:
    if not isinstance(v, str): return v
    s = v.strip().lower()
    if s in {"iced", "ice", "아이스", "차가운", "차가움", "차가운거", "차가운걸"}: return "ice"
    if s in {"hot", "뜨거운", "뜨거움", "핫", "따뜻한", "따뜻한거", "따뜻한걸", "따듯", "따듯한", "따듯한거", "따듯한걸"}: return "hot"
    return v


_ITEM_NOISE_PATTERNS: List[Tuple[str, str]] = [
    (r"(아이스|차가운(거|걸)?|시원한(거|걸)?|iced|ice)\b", " "),
    (r"(뜨거운(거|걸)?|따뜻한(거|걸)?|따듯(한)?(거|걸)?|hot|핫)\b", " "),
    (r"(스몰|small|미디움|medium|라지|large)\b", " "),
    (r"\b(S|M|L)\b", " "),
    (r"(작은(거|걸)?|중간(거|걸)?|큰(거|걸)?|보통(거|걸)?)\b", " "),
    (r"(사이즈|size)\b", " "),
    (r"(두\s*개|2\s*개|두\s*잔|2\s*잔)\b", " "),
    (r"(한\s*개|1\s*개|한\s*잔|1\s*잔)\b", " "),
    (r"(세\s*개|3\s*개|세\s*잔|3\s*잔)\b", " "),
    (r"(주세요|주문|부탁|할게(요)?|줘|주실래요|좀)\b", " "),
]


def _compact_spaces(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s.strip()


def _recover_item_name_candidates(item_name: Any, option_groups: Dict[str, Any]) -> List[str]:
    if not isinstance(item_name, str): return []
    raw = _compact_spaces(item_name)
    if not raw: return []
    cands: List[str] = [raw]
    s = raw
    for pat, rep in _ITEM_NOISE_PATTERNS:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    s = _compact_spaces(s)
    if s and s not in cands: cands.append(s)
    return [x for x in cands if len(x) >= 2]


def _edu_make_llm_task(*, intent: str, slots: Dict[str, Any], meta: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    safe_state = {
        "conversation_id": state.get("conversation_id"),
        "turn_index": state.get("turn_index"),
        "history_summary": state.get("history_summary", ""),
        "active_intent": state.get("active_intent"),
        "slots": state.get("slots", {}),
        "last_bot_action": state.get("last_bot_action"),
    }
    
    # [Context Fix] 교육 관련 메타 데이터 필드 누락 방지
    safe_meta = {
        "locale": meta.get("locale"),
        "timezone": meta.get("timezone"),
        "device_type": meta.get("device_type"),
        "mode": meta.get("mode"),
        "input_type": meta.get("input_type"),
        "user_level": meta.get("user_level"),
        "user_age_group": meta.get("user_age_group"),
        "subject": meta.get("subject"),
        "tone_style": meta.get("tone_style"),
        "target_exam": meta.get("target_exam"),
        "native_language": meta.get("native_language"),
    }
    
    return {
        "type": "edu_answer_generation",
        "input": {"intent": intent, "slots": slots or {}, "meta": safe_meta, "state": safe_state},
        "output_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
                "ui_hints": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"domain": {"type": "string"}, "intent": {"type": "string"}},
                    "required": ["domain", "intent"],
                },
            },
            "required": ["text", "ui_hints"],
        },
    }


def _check_driving_safety_with_llm(intent: str, slots: Dict[str, Any], meta: Dict[str, Any], current_status: Dict[str, Any]) -> Dict[str, Any]:
    """Agentic Safety: LLM을 활용한 논리적 위험 및 중복 판단"""
    simple_slots = {k: _slot_value(slots, k) for k in slots}
    
    system_prompt = (
        "You are the 'Safety Brain' of a smart car.\n"
        "Analyze the User Request against the Vehicle Status.\n"
        "\n"
        "[Mapping Note]\n"
        "- 'steering_wheel' request -> check 'steering_wheel_heat' status.\n"
        "- 'trunk' request -> check 'trunk' status.\n"
        "\n"
        "[Rules]\n"
        "1. **Status Check (Redundancy)**:\n"
        "   - Compare the Request Action vs Current Status Value.\n"
        "   - If they are already in that state (e.g., Request 'Open' but already 'Open'), it is a Redundancy.\n"
        "\n"
        "2. **Safety Risk (Gear Check)**:\n"
        "   - Opening trunk/frunk/door is SAFE only if gear is 'P'.\n"
        "   - If gear is D, R, or N, it is UNSAFE.\n"
        "\n"
        "3. **Context Conflict**:\n"
        "   - e.g., Heater when outside is very hot (>28C).\n"
        "\n"
        "Return JSON only: { 'is_safe': bool, 'response_type': 'execute'|'confirm_conflict'|'reject', 'reason_kor': str }"
    )

    user_prompt = f"Vehicle Status: {json.dumps(current_status)}\nRequest Slots: {json.dumps(simple_slots)}"

    try:
        resp_str = answer_with_openai(user_message=user_prompt, system_prompt=system_prompt, model="gpt-4o-mini", temperature=0.0)
        return json.loads(re.sub(r"```json|```", "", resp_str).strip())
    except Exception:
        return {"is_safe": True, "response_type": "execute", "reason_kor": ""}


def validate_and_build_action(
    *,
    domain: str,
    intent: str,
    slots: Dict[str, Any],
    meta: Dict[str, Any],
    state: Dict[str, Any],
    trace_id: Optional[str] = None,
    catalog: Optional[CatalogRepo] = None,
):
    message_key_ok = f"result.{domain}.{intent}"
    message_key_fail = "result.fail.generic"

    if domain == "driving":
        if intent == "general_chat":
            user_query = str(_slot_value(slots, "query") or "대화")
            return {
                "reply": {
                    "action_type": "answer",
                    "text": user_query,
                    "ui_hints": {"domain": domain, "intent": intent},
                    "message_key_ok": f"result.{domain}.{intent}",
                    "payload": {"intent": intent, "status": "general_chat", "query": user_query}
                }
            }, _merge_state(state, {"current_domain": domain, "active_intent": intent})

        # [상태 동기화: StateManager 활용]
        meta_status = meta.get("vehicle_status") or {}
        saved_status = state.get("vehicle_status") or {}
        current_status = VehicleStateManager.sync_status(saved_status, meta_status)

        # 1. 정책 및 안전 검증
        supported_features = meta.get("supported_features") 
        conflict_reason = check_action_validity(intent, slots, current_status, supported_features)

        if conflict_reason == "feature_not_supported":
            return {
                "reply": {
                    "action_type": "answer",
                    "text": "지원하지 않는 기능입니다.", 
                    "ui_hints": {"domain": domain, "intent": intent, "status": "unsupported"},
                    "payload": {"intent": intent, "status": "unsupported"}
                }
            }, _merge_state(state, {"current_domain": domain, "active_intent": intent})
        
        elif conflict_reason == "unsafe_driving":
            return {
                "reply": {
                    "action_type": "answer",
                    "text": "주행 중에는 해당 기능을 사용할 수 없습니다.",
                    "ui_hints": {"domain": domain, "intent": intent, "status": "rejected"},
                    "payload": {"status": "rejected", "reasoning": "주행 중 안전 차단"}
                }
            }, _merge_state(state, {"current_domain": domain, "active_intent": intent})

        # 2. 톤 가이드 및 LLM 안전 검사
        tone_guidance = "neutral"
        if intent == "control_hardware":
            part_slot = str(_slot_value(slots, "target_part") or "")
            if part_slot in ["seat_heater", "steering_wheel"]: tone_guidance = "warm"
            elif part_slot == "seat_ventilation": tone_guidance = "cool"
        
        safety_result = _check_driving_safety_with_llm(intent, slots, meta, current_status)
        if safety_result.get("response_type") in ["confirm_conflict", "reject"]:
            status = "conflict_confirm" if safety_result["response_type"] == "confirm_conflict" else "rejected"
            return {
                "reply": {
                    "action_type": "answer",
                    "text": safety_result.get("reason_kor", "처리할 수 없는 요청입니다."),
                    "ui_hints": {"domain": domain, "intent": intent, "status": status},
                    "payload": {"intent": intent, "status": status, "tone_guidance": tone_guidance}
                }
            }, _merge_state(state, {"debug_last_reason": f"llm_safety_{status}"})

        # 3. 명령 빌드 및 상태 시뮬레이션
        command_payload = build_vehicle_command(intent, slots)
        params = command_payload.get("params", {})
        
        if intent == "control_hardware":
            updated_status = VehicleStateManager.simulate_action(
                current_status, params.get("part"), params.get("action"), params.get("location_detail", "")
            )
            ko_part = _KO_PART.get(params.get("part"), params.get("part"))
            ko_act = _KO_ACTION.get(params.get("action"), params.get("action"))
            base_text = f"{ko_part} {ko_act}."
        elif intent == "control_hvac":
            updated_status = current_status
            base_text = "공조 장치를 조절합니다."
        elif intent == "navigate_to":
            updated_status = current_status
            base_text = f"{params.get('destination')}로 안내를 시작합니다."
        else:
            updated_status = current_status
            base_text = "요청을 처리했습니다."

        facts = dict(params)
        facts.update({"intent": intent, "status": "success"})

        return {
            "reply": {
                "action_type": "vehicle_action",
                "text": base_text, 
                "command": command_payload,
                "ui_hints": {"domain": domain, "intent": intent, "tone_guidance": tone_guidance},
                "message_key_ok": message_key_ok,
                "payload": facts
            }
        }, _merge_state(state, {"current_domain": domain, "active_intent": intent, "vehicle_status": updated_status})

    # [Kiosk Domain]
    if domain == "kiosk" and intent == "add_item":
        item_name = _slot_value(slots, "item_name")
        quantity = _slot_value(slots, "quantity") or 1
        option_groups_raw = _slot_value(slots, "option_groups")
        option_groups = _normalize_option_groups(option_groups_raw)
        store_id = meta.get("store_id")
        kiosk_type = meta.get("kiosk_type")

        if not store_id or not kiosk_type or not item_name:
            return {
                "reply": {
                    "action_type": "answer",
                    "text": "메뉴 정보를 확인하지 못했어요.",
                    "ui_hints": {"domain": domain, "intent": intent},
                }
            }, _merge_state(state, {"debug_last_reason": "missing_meta_or_item_name"})

        if catalog is None: catalog = default_catalog_repo()
        item = catalog.get_item_by_name(store_id=store_id, kiosk_type=kiosk_type, name=item_name)
        
        if not item:
            return {
                "reply": {
                    "action_type": "answer",
                    "text": f"'{item_name}' 메뉴를 찾지 못했어요.",
                    "ui_hints": {"domain": domain, "intent": intent},
                }
            }, _merge_state(state, {"debug_last_reason": "menu_not_found"})

        required_groups = get_required_option_groups_for_add_item(req={"meta": meta}, slots=slots, catalog=catalog)
        missing_group = find_missing_required_option_group(required_groups=required_groups, option_groups_slot=option_groups)

        if missing_group:
            choices = item.option_groups.get(missing_group) if item.option_groups else None
            return {
                "reply": {
                    "action_type": "ask_option_group",
                    "text": f"{missing_group} 옵션을 선택해 주세요.",
                    "ui_hints": {"domain": domain, "intent": intent, "expect_option_group": missing_group, "choices": choices},
                }
            }, _merge_state(state, {"pending_option_group": missing_group})

        return {
            "reply": {
                "action_type": "add_to_cart",
                "text": f"{item.name} 장바구니에 담았습니다.",
                "ui_hints": {"domain": domain, "intent": intent},
                "payload": {"item_id": item.item_id, "name": item.name, "quantity": quantity, "option_groups": option_groups},
            }
        }, _merge_state(state, {"active_intent": None, "slots": {}})

    # [Education Domain]
    if domain == "education":
        new_state = _merge_state(state, {"current_domain": "education", "active_intent": intent})
        llm_task = _edu_make_llm_task(intent=intent, slots=slots, meta=meta, state=new_state)
        return {
            "reply": {
                "text": "처리할게요.",
                "action_type": "answer",
                "ui_hints": {"domain": domain, "intent": intent},
                "message_key_ok": message_key_ok,
            },
            "llm_task": llm_task,
        }, new_state

    # [Fallback]
    return {
        "reply": {
            "text": "처리할게요.",
            "action_type": "answer",
            "ui_hints": {"domain": domain, "intent": intent},
            "message_key_ok": message_key_ok,
        }
    }, _merge_state(state, {"debug_last_reason": "fallback"})