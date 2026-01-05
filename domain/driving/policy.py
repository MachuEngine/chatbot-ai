# domain/driving/policy.py
from typing import Any, Dict, Optional, List

def _norm(val: Any) -> str:
    if not isinstance(val, str): return ""
    return val.lower().strip()

def _get_slot_value(slots: Dict[str, Any], key: str) -> Any:
    v = slots.get(key)
    if isinstance(v, dict): return v.get("value")
    return v

def check_action_validity(
    intent: str, 
    slots: Dict[str, Any], 
    current_status: Dict[str, Any],
    supported_features: Optional[List[str]] = None
) -> Optional[str]:
    """
    1. 기능 지원 여부(Supported Features) 체크 (Hard Constraint)
    * 참고: 논리/상태 충돌(Context Conflict)은 Validator의 LLM 단계에서 처리함.
    """
    if not current_status:
        return None

    # 지원 기능 리스트가 없으면(None) 모든 기능 지원으로 간주
    use_support_check = (supported_features is not None)

    # 1. 하드웨어 제어
    if intent == "control_hardware":
        part = _norm(_get_slot_value(slots, "target_part"))
        # LLM이 판단하므로 action 슬롯은 여기서 굳이 체크하지 않아도 됨
        loc = _norm(_get_slot_value(slots, "location_detail"))

        # (A) Feature Name Mapping
        feature_name = part # default
        if part == "seat_heater":
            if loc in ["rear", "rear_left", "rear_right"]: feature_name = "seat_heater_rear"
            else: feature_name = "seat_heater_front"
        elif part == "seat_ventilation":
            if loc in ["rear", "rear_left", "rear_right"]: feature_name = "seat_ventilation_rear"
            else: feature_name = "seat_ventilation_front"
        elif part == "window":
            feature_name = "window" 
        elif part == "sunroof":
            feature_name = "sunroof"
        elif part == "steering_wheel":
            feature_name = "steering_wheel_heater"
        
        # (B) Support Check
        basic_parts = ["door_lock", "light", "wiper", "mirror", "trunk", "frunk"] 
        
        if use_support_check:
            if part not in basic_parts and feature_name not in supported_features:
                 if part != "window": 
                     return "feature_not_supported"

        # (C) Status Conflict Check (삭제됨)
        # LLM이 "이미 열려 있습니다" 등의 맥락을 판단하므로 여기서는 물리적 지원 여부만 봅니다.
        pass

    # 2. 공조 제어
    elif intent == "control_hvac":
        seat_loc = _norm(_get_slot_value(slots, "seat_location"))
        
        # (A) Support Check 강화
        if use_support_check:
            # 뒷좌석 공조 요청 시
            if seat_loc == "rear" and "hvac_rear" not in supported_features:
                return "feature_not_supported"
            # 조수석 공조 요청 시
            if seat_loc == "passenger" and "hvac_passenger" not in supported_features:
                return "feature_not_supported"
            
            # 메인 공조(운전석/전체) 요청 시
            if not seat_loc or seat_loc in ["driver", "all"]:
                 # hvac 관련 기능이 하나라도 있는지 확인
                 has_hvac = any("hvac" in f for f in supported_features)
                 if not has_hvac:
                     return "feature_not_supported"

        # (B) Conflict Check (삭제됨)
        # "이미 꺼져있는데 끄라고 함" 등의 로직은 LLM Reasoning으로 이동.
        pass

    return None


def build_vehicle_command(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    """
    NLU 슬롯 결과를 차량 제어용 JSON 커맨드로 변환 (온도 보정 로직 포함)
    """
    command = {"type": "none", "params": {}}

    if intent == "control_hvac":
        action = _norm(_get_slot_value(slots, "action")) or "on"
        mode = _norm(_get_slot_value(slots, "hvac_mode"))
        temp_val = _get_slot_value(slots, "target_temp")
        
        final_temp = temp_val
        
        # 기본 온도 설정
        if final_temp is None:
            if mode == "heat": final_temp = 28
            elif mode == "cool": final_temp = 18
            elif mode == "auto" or action == "on": final_temp = 22
        
        # 온도 범위 제한 (Clamping)
        if final_temp is not None:
            try:
                ft = int(final_temp)
                if ft < 16: ft = 16
                if ft > 32: ft = 32
                final_temp = ft
            except:
                final_temp = 22

        if action == "off":
            final_temp = None

        command["type"] = "hvac_control"
        command["params"] = {
            "action": action,
            "hvac_mode": mode,
            "target_temp": final_temp,
            "seat_location": _get_slot_value(slots, "seat_location"),
            "fan_speed": _get_slot_value(slots, "fan_speed"),
        }

    elif intent == "control_hardware":
        command["type"] = "hardware_control"
        command["params"] = {
            "part": _norm(_get_slot_value(slots, "target_part")),
            "action": _norm(_get_slot_value(slots, "action")),
            "location_detail": _get_slot_value(slots, "location_detail"),
        }

    elif intent == "navigate_to":
        command["type"] = "navigation"
        command["params"] = {
            "destination": _get_slot_value(slots, "destination"),
            "waypoint": _get_slot_value(slots, "waypoint"),
        }
    elif intent == "find_poi":
        command["type"] = "search_poi"
        command["params"] = {
            "poi_type": _get_slot_value(slots, "poi_type"),
            "sort_by": _get_slot_value(slots, "sort_by"),
        }
    
    return command