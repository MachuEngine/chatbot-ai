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

    # 지원 기능 리스트가 없으면 모든 기능 지원으로 간주
    use_support_check = (supported_features is not None)

    # 1. 하드웨어 제어
    if intent == "control_hardware":
        part = _norm(_get_slot_value(slots, "target_part"))
        # (A) Feature Name Mapping
        feature_name = part # default
        if part == "seat_heater":
            loc = _norm(_get_slot_value(slots, "location_detail"))
            if loc in ["rear", "rear_left", "rear_right"]: feature_name = "seat_heater_rear"
            else: feature_name = "seat_heater_front"
        elif part == "seat_ventilation":
            loc = _norm(_get_slot_value(slots, "location_detail"))
            if loc in ["rear", "rear_left", "rear_right"]: feature_name = "seat_ventilation_rear"
            else: feature_name = "seat_ventilation_front"
        elif part == "steering_wheel":
            feature_name = "steering_wheel_heater"
        
        # (B) Support Check
        basic_parts = ["door_lock", "light", "wiper", "mirror", "trunk", "frunk", "window"] 
        
        if use_support_check:
            if part not in basic_parts and feature_name not in supported_features:
                 return "feature_not_supported"

    # 2. 공조 제어
    elif intent == "control_hvac":
        seat_loc = _norm(_get_slot_value(slots, "seat_location"))
        
        # (A) Support Check
        if use_support_check:
            if seat_loc == "rear" and "hvac_rear" not in supported_features:
                return "feature_not_supported"
            if seat_loc == "passenger" and "hvac_passenger" not in supported_features:
                return "feature_not_supported"

        # (B) Status Conflict (Legacy)
        # LLM이 판단하겠지만, 명백하게 '이미 켜져있음' 같은 단순 상태는 여기서 걸러도 됨.
        # 단, 사용자 경험상 LLM이 "이미 켜져 있습니다"라고 말해주는게 더 자연스러우므로 
        # 여기서는 하드웨어 미지원(Critical)만 체크하고 나머지는 Pass.
        pass

    return None

def build_vehicle_command(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    """
    NLU 슬롯 결과를 차량 제어용 JSON 커맨드로 변환
    """
    command = {"type": "none", "params": {}}

    if intent == "control_hvac":
        command["type"] = "hvac_control"
        command["params"] = {
            "action": _norm(_get_slot_value(slots, "action")) or "on",
            "hvac_mode": _norm(_get_slot_value(slots, "hvac_mode")),
            "target_temp": _get_slot_value(slots, "target_temp"),
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