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
    2. 주행 안전(Gear/Driving) 체크 (Safety Constraint)
    """
    if not current_status:
        return None

    # 지원 기능 리스트가 없으면(None) 모든 기능 지원으로 간주
    use_support_check = (supported_features is not None)

    # 1. 하드웨어 제어
    if intent == "control_hardware":
        part = _norm(_get_slot_value(slots, "target_part"))
        action = _norm(_get_slot_value(slots, "action"))
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

        # (C) Driving Safety Check (Gear Check)
        # 기어가 P가 아니면(D, R, N 등) 주행 중으로 간주
        gear = _norm(current_status.get("gear"))
        is_driving = gear in ["d", "r", "n", "drive", "reverse", "neutral"]
        
        if is_driving:
            # 주행 중 금지 목록: 문, 트렁크, 프렁크, 충전구 열기/잠금해제
            # (창문, 선루프, 와이퍼, 라이트, 열선 등은 주행 중에도 허용)
            unsafe_parts = ["door_lock", "trunk", "frunk", "charge_port"]
            unsafe_actions = ["open", "unlock"]
            
            if part in unsafe_parts and action in unsafe_actions:
                return "unsafe_driving"

    # 2. 공조 제어
    elif intent == "control_hvac":
        seat_loc = _norm(_get_slot_value(slots, "seat_location"))
        
        # (A) Support Check
        if use_support_check:
            if seat_loc == "rear" and "hvac_rear" not in supported_features:
                return "feature_not_supported"
            if seat_loc == "passenger" and "hvac_passenger" not in supported_features:
                return "feature_not_supported"
            
            if not seat_loc or seat_loc in ["driver", "all"]:
                 has_hvac = any("hvac" in f for f in supported_features)
                 if not has_hvac:
                     return "feature_not_supported"

        # HVAC는 주행 중 제어 허용 (Safety Check Pass)

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