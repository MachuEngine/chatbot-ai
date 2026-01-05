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
_TEMP_WORDS = ["아이스", "차가운", "시원한", "iced", "ice", "뜨거운", "따뜻한", "따듯", "hot", "핫"]
_SIZE_WORDS = ["스몰", "small", "미디움", "medium", "라지", "large", "작은", "중간", "큰", "보통", "S", "M", "L"]


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
    s2 = raw
    if option_groups.get("temperature") is not None:
        for w in _TEMP_WORDS: s2 = re.sub(rf"\b{re.escape(w)}\b", " ", s2, flags=re.IGNORECASE)
    if option_groups.get("size") is not None:
        for w in _SIZE_WORDS: s2 = re.sub(rf"\b{re.escape(w)}\b", " ", s2, flags=re.IGNORECASE)
    s2 = _compact_spaces(s2)
    if s2 and s2 not in cands: cands.append(s2)
    out: List[str] = []
    for x in cands:
        if len(x) >= 2: out.append(x)
    return out


def _edu_make_llm_task(*, intent: str, slots: Dict[str, Any], meta: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    safe_state = {
        "conversation_id": state.get("conversation_id"),
        "turn_index": state.get("turn_index"),
        "history_summary": state.get("history_summary", ""),
        "active_intent": state.get("active_intent"),
        "slots": state.get("slots", {}),
        "last_bot_action": state.get("last_bot_action"),
    }
    safe_meta = {
        "locale": meta.get("locale"),
        "timezone": meta.get("timezone"),
        "device_type": meta.get("device_type"),
        "mode": meta.get("mode"),
        "input_type": meta.get("input_type"),
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

# Agentic Safety & Logic Check
def _check_driving_safety_with_llm(intent: str, slots: Dict[str, Any], meta: Dict[str, Any], current_status: Dict[str, Any]) -> Dict[str, Any]:
    """
    차량 제어 요청에 대해 현재 상태(Context)와 대조하여 안전하고 논리적인지 판단.
    Rule-Base 대신 LLM이 판단 (예: 33도에 히터 켜기 -> Unsafe, 이미 켜져있음 -> Redundancy)
    """
    user_message = meta.get("user_message_preview") or "사용자 요청"
    simple_slots = {k: _slot_value(slots, k) for k in slots}
    
    # [수정] 프롬프트 보강: Toggle Action과 Redundancy를 명확히 구분
    system_prompt = (
        "You are the 'Safety Brain' of a smart car.\n"
        "Analyze the User Request against the Vehicle Status.\n"
        "\n"
        "[Rules]\n"
        "1. **Status Check (Crucial)**:\n"
        "   - Request 'Open' + Status 'Closed' => **Safe/Execute** (Normal action).\n"
        "   - Request 'Close' + Status 'Open' => **Safe/Execute** (Normal action).\n"
        "   - Request 'Open' + Status 'Open' => **Redundancy/Reject** ('Already open').\n"
        "   - Request 'Close' + Status 'Closed' => **Redundancy/Reject** ('Already closed').\n"
        "   - Request 'On' + Status 'On' => **Redundancy/Reject** ('Already on').\n"
        "\n"
        "2. **Context Conflict**:\n"
        "   - Heater when hot (>28C) or AC when cold (<15C) => **Confirm Conflict**.\n"
        "\n"
        "3. **Safety Risk**:\n"
        "   - Opening doors/trunk while driving => **Reject**.\n"
        "\n"
        "Return JSON only:\n"
        "{\n"
        '  "is_safe": bool,\n'
        '  "response_type": "execute" | "confirm_conflict" | "reject",\n'
        '  "reason_kor": "Short explanation in Korean."\n'
        "}"
    )

    user_prompt = (
        f"Vehicle Status: {json.dumps(current_status, ensure_ascii=False)}\n"
        f"User Request Intent: {intent}\n"
        f"Request Slots: {json.dumps(simple_slots, ensure_ascii=False)}\n"
    )

    try:
        resp_str = answer_with_openai(
            user_message=user_prompt,
            system_prompt=system_prompt,
            model="gpt-4o-mini",
            temperature=0.0
        )
        clean_json = re.sub(r"```json|```", "", resp_str).strip()
        result = json.loads(clean_json)
        return result
    except Exception as e:
        log_event(None, "safety_check_error", {"error": str(e)})
        return {"is_safe": True, "response_type": "execute", "reason_kor": ""}

# [추가] 상태 시뮬레이터: 성공한 액션을 상태에 반영
def _update_vehicle_status_simulation(current_status: Dict[str, Any], intent: str, params: Dict[str, Any]) -> Dict[str, Any]:
    new_status = dict(current_status)
    
    # 맵핑: Action -> Value
    # (주의: 실제 차에서는 'on'/'off'와 'open'/'closed'가 다를 수 있으나, 여기선 시뮬레이션용 단순화)
    act_map = {
        "open": "open", "close": "closed",
        "on": "on", "off": "off",
        "lock": "locked", "unlock": "unlocked"
    }
    
    if intent == "control_hardware":
        part = str(params.get("part") or "")
        act = str(params.get("action") or "")
        val = act_map.get(act)
        
        if val:
            # 주요 파츠 상태 업데이트
            if part == "sunroof": new_status["sunroof"] = val
            elif part == "charge_port": new_status["charge_port"] = val
            elif part == "trunk": new_status["trunk"] = val
            elif part == "frunk": new_status["frunk"] = val
            elif part == "door_lock": new_status["door_lock"] = val
            elif part == "window":
                # 전체 윈도우 닫기/열기 시뮬레이션 (단순화)
                new_status["window_driver"] = val
                new_status["window_passenger"] = val
                new_status["window_rear_left"] = val
                new_status["window_rear_right"] = val

    elif intent == "control_hvac":
        act = str(params.get("action") or "")
        mode = str(params.get("hvac_mode") or "")
        temp = params.get("target_temp")
        
        if act == "on": new_status["hvac_power"] = "on"
        elif act == "off": new_status["hvac_power"] = "off"
        
        if mode: new_status["hvac_mode"] = mode
        if temp: new_status["target_temp"] = temp
        # 온도 변경에 따른 현재온도 추종 시뮬레이션은 생략 (복잡도 때문)
        
    return new_status


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
        # 1. 잡담(General Chat)
        if intent == "general_chat":
            user_query = str(_slot_value(slots, "query") or "")
            if not user_query: user_query = "대화"
            
            action = {
                "reply": {
                    "action_type": "answer",
                    "text": user_query,
                    "ui_hints": {"domain": domain, "intent": intent},
                    "message_key_ok": f"result.{domain}.{intent}",
                    "payload": {"intent": intent, "status": "general_chat", "query": user_query}
                }
            }
            new_state = _merge_state(state, {"current_domain": domain, "active_intent": intent})
            return action, new_state

        # [상태 동기화] 저장된 상태가 있다면 메타보다 우선순위 높게 적용 (상태 유지성 확보)
        # 1순위: State(이전 턴에서 업데이트됨), 2순위: Meta(클라이언트 원본)
        meta_status = meta.get("vehicle_status") or {}
        saved_status = state.get("vehicle_status")
        current_status = saved_status if saved_status else meta_status

        # [Step 1] Hardware Support Check (우선순위 높임)
        supported_features = meta.get("supported_features") 
        conflict_reason = check_action_validity(intent, slots, current_status, supported_features)

        if conflict_reason == "feature_not_supported":
            action = {
                "reply": {
                    "action_type": "answer",
                    "text": "지원하지 않는 기능입니다.", 
                    "message_key": "result.driving.conflict.feature_not_supported", 
                    "ui_hints": {"domain": domain, "intent": intent, "status": "unsupported"},
                    "payload": {"intent": intent, "status": "unsupported"}
                }
            }
            new_state = _merge_state(state, {"current_domain": domain, "active_intent": intent})
            return action, new_state

        # [Step 2] Agentic Safety Check (LLM Reasoning)
        if intent in ["control_hvac", "control_hardware"]:
            tone_guidance = "neutral"
            effective_mode = ""

            if intent == "control_hvac":
                slot_mode = str(_slot_value(slots, "hvac_mode") or "").lower().strip()
                effective_mode = slot_mode
                if not effective_mode:
                    effective_mode = str(current_status.get("hvac_mode") or "").lower().strip()
                
                if effective_mode in ["cool", "ac"]: tone_guidance = "cool"
                elif effective_mode in ["heat", "heater"]: tone_guidance = "warm"
            
            # 여기서 current_status(업데이트된 상태)를 넘겨야 올바른 판단 가능
            safety_result = _check_driving_safety_with_llm(intent, slots, meta, current_status)
            
            # (A) 갈등/확인 필요
            if safety_result.get("response_type") == "confirm_conflict":
                reason = safety_result.get("reason_kor") or "현재 상황과 맞지 않는 요청입니다."
                action = {
                    "reply": {
                        "action_type": "answer",
                        "text": f"{reason} 그래도 진행할까요?", 
                        "ui_hints": {"domain": domain, "intent": intent, "status": "conflict_confirm"},
                        "message_key_ok": f"result.driving.conflict",
                        "payload": {
                            "intent": intent, 
                            "status": "conflict_confirm", 
                            "reasoning": reason, 
                            "is_safe": False,
                            "tone_guidance": tone_guidance, 
                            "hvac_mode": effective_mode
                        }
                    }
                }
                new_state = _merge_state(state, {"debug_last_reason": "llm_safety_conflict"})
                return action, new_state
            
            # (B) 거절 (Reject - 이미 켜져있음 등)
            elif safety_result.get("response_type") == "reject":
                reason = safety_result.get("reason_kor") or "수행할 수 없는 요청입니다."
                action = {
                    "reply": {
                        "action_type": "answer",
                        "text": reason,
                        "ui_hints": {"domain": domain, "intent": intent, "status": "rejected"},
                        "message_key_ok": f"result.driving.reject",
                        "payload": {
                            "status": "rejected", 
                            "reasoning": reason,
                            "tone_guidance": tone_guidance,
                            "hvac_mode": effective_mode
                        }
                    }
                }
                return action, _merge_state(state, {"debug_last_reason": "llm_safety_reject"})

        # --- [Step 3] Success Execution ---
        command_payload = build_vehicle_command(intent, slots)
        params = command_payload.get("params", {})
        
        # [상태 업데이트] 성공한 명령을 시뮬레이션하여 상태에 반영
        updated_status = _update_vehicle_status_simulation(current_status, intent, params)

        base_text = "요청을 처리합니다."
        
        cmd_mode = str(params.get("hvac_mode") or "").lower().strip()
        final_mode = cmd_mode if cmd_mode else ""
        
        final_tone = "neutral"

        if intent == "control_hvac":
             check_mode = final_mode if final_mode else effective_mode
             if check_mode in ["cool", "ac"]: final_tone = "cool"
             elif check_mode in ["heat", "heater"]: final_tone = "warm"

        if intent == "control_hardware":
            part = str(params.get("part") or "")
            act = str(params.get("action") or "")
            ko_part = _KO_PART.get(part, part)
            ko_act = _KO_ACTION.get(act, act)
            base_text = f"{ko_part} {ko_act}."
        
        elif intent == "control_hvac":
            act = str(params.get("action") or "")
            ko_act = _KO_ACTION.get(act, act)
            
            if final_tone == "cool":
                base_text = f"에어컨을 켜서 시원하게 합니다."
            elif final_tone == "warm":
                base_text = f"히터를 켜서 따뜻하게 합니다."
            else:
                base_text = f"공조장치를 {ko_act}."

        facts = dict(params)
        facts["intent"] = intent
        facts["status"] = "success"

        action = {
            "reply": {
                "action_type": "vehicle_action",
                "text": base_text, 
                "command": command_payload,
                "ui_hints": {
                    "domain": domain, "intent": intent, "tone_guidance": final_tone
                },
                "message_key_ok": message_key_ok,
                "payload": facts, 
            }
        }
        
        # [State 저장] 업데이트된 vehicle_status를 다음 턴을 위해 저장
        new_state = _merge_state(state, {
            "current_domain": domain, 
            "active_intent": intent,
            "vehicle_status": updated_status # <- 상태 저장
        })
        return action, new_state

    # [Kiosk / Add Item] (기존 유지)
    if domain == "kiosk" and intent == "add_item":
        # (기존 코드 생략 없이 포함)
        item_name = _slot_value(slots, "item_name")
        quantity = _slot_value(slots, "quantity") or 1
        option_groups_raw = _slot_value(slots, "option_groups")
        option_groups = _normalize_option_groups(option_groups_raw)
        if "temperature" in option_groups:
            option_groups["temperature"] = _normalize_temperature_value(option_groups.get("temperature"))
        store_id = meta.get("store_id")
        kiosk_type = meta.get("kiosk_type")

        if not store_id or not kiosk_type or not item_name:
            action = {
                "reply": {
                    "action_type": "answer",
                    "text": "메뉴 정보를 확인하지 못했어요. 다시 한 번 말씀해 주세요.",
                    "ui_hints": {"domain": domain, "intent": intent},
                    "message_key_ok": message_key_ok,
                    "message_key_fail": message_key_fail,
                }
            }
            new_state = _merge_state(state, {"debug_last_reason": "missing_meta_or_item_name"})
            return action, new_state

        if catalog is None:
            catalog = default_catalog_repo()

        item = catalog.get_item_by_name(store_id=store_id, kiosk_type=kiosk_type, name=item_name)
        used_name = item_name
        recovered = False
        if not item:
            cands = _recover_item_name_candidates(item_name, option_groups)
            for cand in cands:
                it2 = catalog.get_item_by_name(store_id=store_id, kiosk_type=kiosk_type, name=cand)
                if it2:
                    item = it2
                    used_name = cand
                    recovered = True
                    break
            if log_event and trace_id:
                log_event(trace_id, "validator_item_lookup_retry", {"original": item_name, "candidates": cands, "recovered": recovered})

        if not item:
            action = {
                "reply": {
                    "action_type": "answer",
                    "text": f"'{item_name}' 메뉴를 찾지 못했어요. 다른 메뉴를 선택해 주세요.",
                    "ui_hints": {"domain": domain, "intent": intent},
                    "message_key_ok": message_key_ok,
                    "message_key_fail": message_key_fail,
                }
            }
            new_state = _merge_state(state, {"debug_last_reason": "menu_not_found"})
            return action, new_state

        slots_for_policy = dict(slots or {})
        if recovered:
            slots_for_policy["item_name"] = {"value": used_name, "confidence": 0.6}

        required_groups = get_required_option_groups_for_add_item(req={"meta": meta}, slots=slots_for_policy, catalog=catalog)
        missing_group = find_missing_required_option_group(required_groups=required_groups, option_groups_slot=option_groups)

        if missing_group:
            prompt_map = {"temperature": "뜨거운/아이스 중 어떤 걸로 드릴까요?", "size": "사이즈는 어떤 걸로 드릴까요? (S/M/L)"}
            text = prompt_map.get(missing_group, f"{missing_group} 옵션을 선택해 주세요.")
            choices = None
            if item.option_groups:
                choices = item.option_groups.get(missing_group)
            action = {
                "reply": {
                    "action_type": "ask_option_group",
                    "text": text,
                    "ui_hints": {"domain": domain, "intent": intent, "expect_option_group": missing_group, "choices": choices},
                }
            }
            slots_min = {
                "item_name": slots.get("item_name"),
                "quantity": slots.get("quantity"),
                "option_groups": {"value": dict(option_groups), "confidence": 0.9},
                "notes": slots.get("notes"),
            }
            new_state = _merge_state(
                state,
                {
                    "current_domain": domain,
                    "active_intent": intent,
                    "slots": slots_min,
                    "pending_option_group": missing_group,
                    "pending_option_group_choices": choices,
                    "last_bot_action": "ask_option_group",
                    "debug_last_reason": f"missing_option_group:{missing_group}",
                },
            )
            return action, new_state

        action = {
            "reply": {
                "action_type": "add_to_cart",
                "text": f"{item.name} {quantity}개를 장바구니에 담았어요.",
                "ui_hints": {"domain": domain, "intent": intent},
                "payload": {"item_id": item.item_id, "name": item.name, "price": item.price, "quantity": quantity, "option_groups": option_groups},
            }
        }
        new_state = _merge_state(
            state,
            {
                "current_domain": domain,
                "active_intent": None,
                "slots": {},
                "last_bot_action": "add_to_cart",
                "debug_last_reason": "added_to_cart",
                "pending_option_group": None,
                "pending_option_group_choices": None,
            },
        )
        return action, new_state

    # [Education Domain]
    if domain == "education":
        new_state = _merge_state(
            state,
            {
                "current_domain": "education",
                "active_intent": intent,
                "slots": slots or {},
                "last_bot_action": "answer",
                "debug_last_reason": f"edu:llm_generate:{intent}",
            },
        )
        llm_task = _edu_make_llm_task(intent=intent, slots=slots, meta=meta, state=new_state)
        action = {
            "reply": {
                "text": "처리할게요.",
                "action_type": "answer",
                "ui_hints": {"domain": domain, "intent": intent},
                "message_key_ok": message_key_ok,
                "message_key_fail": message_key_fail,
            },
            "llm_task": llm_task,
        }
        return action, new_state

    # [Fallback]
    action = {
        "reply": {
            "text": TEMPLATES.get(message_key_ok, "") or "처리할게요.",
            "action_type": "answer",
            "ui_hints": {"domain": domain, "intent": intent},
            "message_key_ok": message_key_ok,
            "message_key_fail": message_key_fail,
        }
    }
    new_state = _merge_state(state, {"debug_last_reason": "action:planned"})
    return action, new_state