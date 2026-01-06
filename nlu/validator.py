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
    
    # [Fix] Education Context Fields 누락 수정
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

# Agentic Safety & Logic Check
def _check_driving_safety_with_llm(intent: str, slots: Dict[str, Any], meta: Dict[str, Any], current_status: Dict[str, Any], history: str = "") -> Dict[str, Any]:
    """
    차량 제어 요청에 대해 현재 상태(Context)와 대조하여 안전하고 논리적인지 판단.
    Rule-Base 대신 LLM이 판단.
    """
    user_message = meta.get("user_message_preview") or "사용자 요청"
    simple_slots = {k: _slot_value(slots, k) for k in slots}
    
    # [수정] 프롬프트: REJECT 규칙 우선 순위 상향 + HVAC Power 명시 + Table Format
    system_prompt = (
        "You are the 'Safety Brain' of a smart car. Compare the user's Request vs Current Status.\n"
        "\n"
        "[LOGIC TABLE - FOLLOW STRICTLY IN ORDER]\n"
        "1. **HVAC Power Rule** (If intent='control_hvac' & action='on'/'off'):\n"
        "   - **REJECT CONDITION**: Request 'Off' + Current 'Off' => **REJECT** (Already off)\n"
        "   - **REJECT CONDITION**: Request 'On'  + Current 'On' + (No Mode/Temp Change) => **REJECT** (Already on)\n"
        "   - Request 'Off' + Current 'On'  => **EXECUTE** (Turn off)\n"
        "   - Request 'On'  + Current 'Off' => **EXECUTE** (Turn on)\n"
        "   - Request 'On'  + Current 'On' + (Mode/Temp Change) => **EXECUTE** (Mode Change)\n"
        "\n"
        "2. **General Hardware Rule** (Window, Trunk, Lock, etc.):\n"
        "   - **REJECT CONDITION**: Request 'Open'  + Current 'Open'   => **REJECT**\n"
        "   - **REJECT CONDITION**: Request 'Close' + Current 'Closed' => **REJECT**\n"
        "   - **REJECT CONDITION**: Request 'Lock'  + Current 'Locked'   => **REJECT**\n"
        "   - Request 'Open'  + Current 'Closed' => **EXECUTE**\n"
        "   - Request 'Close' + Current 'Open'   => **EXECUTE**\n"
        "   - Request 'Lock'  + Current 'Unlocked' => **EXECUTE**\n"
        "\n"
        "[Mapping Instructions]\n"
        "- For HVAC Power, look at key: 'hvac_power' (Not hvac_mode).\n"
        "- For Window/Trunk, look at the specific key (e.g. 'window_driver').\n"
        "\n"
        "Return JSON only:\n"
        "{\n"
        '  "is_safe": bool,\n'
        '  "response_type": "execute" | "confirm_conflict" | "reject",\n'
        '  "reason_kor": "Short explanation in Korean"\n'
        "}"
    )

    user_prompt = (
        f"Recent Conversation History:\n{history}\n\n"
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

# 상태 시뮬레이터: 성공한 액션을 상태에 반영
def _update_vehicle_status_simulation(current_status: Dict[str, Any], intent: str, params: Dict[str, Any]) -> Dict[str, Any]:
    new_status = dict(current_status)
    
    # 맵핑: Action -> Value
    act_map = {
        "open": "open", "close": "closed",
        "on": "on", "off": "off",
        "lock": "locked", "unlock": "unlocked"
    }
    
    if intent == "control_hardware":
        part = str(params.get("part") or "")
        act = str(params.get("action") or "")
        detail = str(params.get("location_detail") or "")
        val = act_map.get(act)
        
        if val:
            if part == "sunroof": new_status["sunroof"] = val
            elif part == "charge_port": new_status["charge_port"] = val
            elif part == "trunk": new_status["trunk"] = val
            elif part == "frunk": new_status["frunk"] = val
            elif part == "door_lock": new_status["door_lock"] = val
            elif part == "window":
                if detail == "all":
                    new_status["window_driver"] = val
                    new_status["window_passenger"] = val
                    new_status["window_rear_left"] = val
                    new_status["window_rear_right"] = val
                elif detail == "driver": new_status["window_driver"] = val
                elif detail == "passenger": new_status["window_passenger"] = val
                else: 
                    new_status["window_driver"] = val
                    new_status["window_passenger"] = val

            # [Logic Update] Seat Heater & Ventilation Mutually Exclusive
            # 열선을 켜면 통풍을 끄고, 통풍을 켜면 열선을 끄는 로직
            elif part == "seat_heater":
                if detail in ["driver", "front", ""]: 
                    new_status["seat_heater_driver"] = val
                    if val == "on": new_status["seat_ventilation_driver"] = "off"
                if detail in ["passenger", "front", ""]: 
                    new_status["seat_heater_passenger"] = val
                    if val == "on": new_status["seat_ventilation_passenger"] = "off"
                if detail in ["rear", "rear_left", "all"]: new_status["seat_heater_rear_left"] = val
                if detail in ["rear", "rear_right", "all"]: new_status["seat_heater_rear_right"] = val
            elif part == "seat_ventilation":
                if detail in ["driver", "front", ""]: 
                    new_status["seat_ventilation_driver"] = val
                    if val == "on": new_status["seat_heater_driver"] = "off"
                if detail in ["passenger", "front", ""]: 
                    new_status["seat_ventilation_passenger"] = val
                    if val == "on": new_status["seat_heater_passenger"] = "off"
            
            elif part == "steering_wheel":
                new_status["steering_wheel_heat"] = val
            elif part == "light":
                new_status["light_head"] = val
            elif part == "wiper":
                new_status["wiper_front"] = val

    elif intent == "control_hvac":
        act = str(params.get("action") or "")
        mode = str(params.get("hvac_mode") or "")
        temp = params.get("target_temp")
        
        if act == "on": new_status["hvac_power"] = "on"
        elif act == "off": new_status["hvac_power"] = "off"
        
        if mode: new_status["hvac_mode"] = mode
        if temp: new_status["target_temp"] = temp
        
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
        # 1. 잡담(General Chat) 개선: LLM에게 답변 생성 요청 (히스토리 포함)
        if intent == "general_chat":
            user_query = str(_slot_value(slots, "query") or "")
            if not user_query: 
                user_query = meta.get("user_message_preview") or "대화"
            
            # [Fix] 대화 히스토리 가져와서 프롬프트에 주입
            history_list = state.get("history", [])
            recent_history = history_list[-10:] # 최근 10개 턴
            history_str = ""
            for h in recent_history:
                role = "User" if h.get("role") == "user" else "Assistant"
                content = h.get("content", "")
                history_str += f"{role}: {content}\n"

            system_prompt = (
                "You are a helpful and witty driving assistant AI.\n"
                "Your user is currently driving. Keep answers concise, safe, and helpful.\n"
                "If the user asks for recommendations (e.g. menu), give a specific and tasty recommendation.\n"
                "Reflect on the Conversation History to understand context (e.g. 'something else').\n"
                "Current Context: Driving Mode."
            )
            
            full_user_input = f"Conversation History:\n{history_str}\n\nUser Request: {user_query}"

            try:
                ai_reply = answer_with_openai(
                    user_message=full_user_input, # [Fix] 히스토리 포함
                    system_prompt=system_prompt,
                    model="gpt-4o-mini",
                    temperature=0.7 
                )
            except Exception:
                ai_reply = "제가 운전 중이라 잠시 딴생각을 했나 봐요. 다시 말씀해 주시겠어요?"

            action = {
                "reply": {
                    "action_type": "answer",
                    "text": ai_reply,
                    "ui_hints": {"domain": domain, "intent": intent},
                    "message_key_ok": f"result.{domain}.{intent}",
                    "payload": {"intent": intent, "status": "general_chat", "query": user_query}
                }
            }
            # [Fix] slots 업데이트 추가
            new_state = _merge_state(state, {"current_domain": domain, "active_intent": intent, "slots": slots})
            return action, new_state

        # [상태 동기화: Meta(현실)가 Saved(기억)을 덮어써야 함]
        meta_status = meta.get("vehicle_status") or {}
        saved_status = state.get("vehicle_status") or {}
        current_status = dict(saved_status)
        current_status.update(meta_status)

        # [Step 1] Hardware Support Check
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
            new_state = _merge_state(state, {"current_domain": domain, "active_intent": intent, "slots": slots})
            return action, new_state
        
        elif conflict_reason == "unsafe_driving":
            action = {
                "reply": {
                    "action_type": "answer",
                    "text": "주행 중에는 해당 기능을 사용할 수 없습니다.",
                    "ui_hints": {"domain": domain, "intent": intent, "status": "rejected"},
                    "message_key_ok": "result.driving.reject", 
                    "payload": {"status": "rejected", "reasoning": "주행 중 안전을 위해 제한된 기능입니다."}
                }
            }
            new_state = _merge_state(state, {"current_domain": domain, "active_intent": intent, "slots": slots})
            return action, new_state

        # [Tone Init]
        tone_guidance = "neutral"
        effective_mode = ""

        # [Step 2] Agentic Safety Check (LLM Reasoning)
        if intent in ["control_hvac", "control_hardware"]:
            # [Patch] NLU Miss Correction
            if intent == "control_hvac":
                user_msg_raw = str(meta.get("user_message_preview") or "").replace(" ", "")
                if _slot_value(slots, "hvac_mode") is None:
                    if "에어컨" in user_msg_raw or "냉방" in user_msg_raw or "에어콘" in user_msg_raw:
                        slots["hvac_mode"] = {"value": "cool", "confidence": 1.0}
                    elif "히터" in user_msg_raw or "난방" in user_msg_raw:
                        slots["hvac_mode"] = {"value": "heat", "confidence": 1.0}

            if intent == "control_hardware":
                part_slot = str(_slot_value(slots, "target_part") or "")
                if part_slot in ["seat_heater", "steering_wheel"]:
                    tone_guidance = "warm"
                elif part_slot in ["seat_ventilation"]:
                    tone_guidance = "cool"
            
            if tone_guidance == "neutral":
                if intent == "control_hvac":
                    slot_mode = str(_slot_value(slots, "hvac_mode") or "").lower().strip()
                    effective_mode = slot_mode
                
                if not effective_mode:
                    effective_mode = str(current_status.get("hvac_mode") or "").lower().strip()
                
                if effective_mode in ["cool", "ac"]: tone_guidance = "cool"
                elif effective_mode in ["heat", "heater"]: tone_guidance = "warm"
            
            # [수정] 대화 히스토리 전달 및 검증
            history_summary = state.get("history_summary", "")
            safety_result = _check_driving_safety_with_llm(intent, slots, meta, current_status, history=history_summary)
            
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
                new_state = _merge_state(state, {
                    "current_domain": domain, 
                    "active_intent": intent,
                    "debug_last_reason": "llm_safety_conflict", 
                    "slots": slots
                })
                return action, new_state
            
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
                new_state = _merge_state(state, {
                    "current_domain": domain, 
                    "active_intent": intent,
                    "debug_last_reason": "llm_safety_reject", 
                    "slots": slots
                })
                return action, new_state

        # --- [Step 3] Success Execution ---
        
        # [스마트 로직] 히터/에어컨 모드 변경 시 적절한 온도 자동 주입
        if intent == "control_hvac":
            mode_slot = str(_slot_value(slots, "hvac_mode") or "").lower()
            temp_slot = _slot_value(slots, "target_temp")
            
            if not temp_slot:
                if mode_slot in ["heat", "heater"]:
                    slots["target_temp"] = {"value": 28, "confidence": 1.0}
                elif mode_slot in ["cool", "ac", "cool mode"]:
                    slots["target_temp"] = {"value": 18, "confidence": 1.0}

        # ✅ [Upgrade] 목적지 모호성 해결 (LLM-based Resolution)
        # 룰 베이스(키워드 리스트)를 제거하고 LLM이 직접 판단 (Context Resolver)
        if intent == "navigate_to":
            dest = str(_slot_value(slots, "destination") or "").strip()
            
            # 목적지가 있으나, 모호한 표현일 가능성이 있으므로 LLM에게 확인
            # (단순 키워드 체크가 아니라 문맥을 통해 구체적 장소인지 확인)
            if dest:
                history_list = state.get("history", [])
                recent_history = history_list[-6:] 
                history_str = ""
                for h in recent_history:
                    role = "User" if h.get("role") == "user" else "Assistant"
                    history_str += f"{role}: {h.get('content')}\n"
                
                # [Smart Prompt]
                resolution_prompt = (
                    "You are a Context Resolver for a Navigation System.\n"
                    "Analyze the User's Destination Request.\n"
                    "\n"
                    "1. IF the request is SPECIFIC (e.g. 'Gangnam Station', 'McDonalds', 'Home'), return it AS IS.\n"
                    "2. IF the request is VAGUE (e.g. 'there', 'that place', 'the restaurant mentioned'), find the specific location from History.\n"
                    "3. IF not found in history, return 'FAIL'.\n"
                    "\n"
                    "Return ONLY the specific location string."
                )
                
                try:
                    resolved = answer_with_openai(
                        user_message=f"History:\n{history_str}\n\nUser Request Destination: {dest}",
                        system_prompt=resolution_prompt,
                        model="gpt-4o-mini",
                        temperature=0.0
                    )
                    
                    clean_resolved = resolved.strip().replace("'", "").replace('"', "")
                    
                    # LLM이 새로운 장소를 찾았거나, 기존 장소를 확정했으면 업데이트
                    if clean_resolved and "FAIL" not in clean_resolved and len(clean_resolved) < 50:
                        slots["destination"] = {"value": clean_resolved, "confidence": 1.0}
                        
                except Exception:
                    pass

        command_payload = build_vehicle_command(intent, slots)
        params = command_payload.get("params", {})
        
        # [상태 업데이트]
        updated_status = _update_vehicle_status_simulation(current_status, intent, params)

        base_text = "요청을 처리합니다."
        final_tone = tone_guidance

        # [Fix] HVAC Text Logic: Explicit check for 'off' action
        if intent == "control_hvac":
             act = str(params.get("action") or "")
             cmd_mode = str(params.get("hvac_mode") or "").lower().strip()
             check_mode = cmd_mode if cmd_mode else effective_mode
             
             if check_mode in ["cool", "ac"]: final_tone = "cool"
             elif check_mode in ["heat", "heater"]: final_tone = "warm"

             if act == "off":
                 base_text = "공조장치를 끕니다."
             else:
                 if final_tone == "cool":
                    base_text = "에어컨을 켜서 시원하게 합니다."
                 elif final_tone == "warm":
                    base_text = "히터를 켜서 따뜻하게 합니다."
                 else:
                    base_text = "공조장치를 켭니다."

        elif intent == "control_hardware":
            part = str(params.get("part") or "")
            act = str(params.get("action") or "")
            ko_part = _KO_PART.get(part, part)
            ko_act = _KO_ACTION.get(act, act)
            base_text = f"{ko_part} {ko_act}."
        
        elif intent == "navigate_to":
            dest = str(params.get("destination") or "")
            base_text = f"{dest}로 안내를 시작합니다."

        facts = dict(params)
        
        if intent == "control_hardware" and facts.get("part") == "steering_wheel":
            facts["part"] = "steering_wheel_heater"
            
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
        
        new_state = _merge_state(state, {
            "current_domain": domain, 
            "active_intent": intent,
            "vehicle_status": updated_status,
            "slots": slots
        })
        return action, new_state

    # [Kiosk / Add Item] (기존 유지)
    if domain == "kiosk" and intent == "add_item":
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
    # [Fix] Fallback시에도 슬롯 갱신
    new_state = _merge_state(state, {"debug_last_reason": "action:planned", "slots": slots})
    return action, new_state