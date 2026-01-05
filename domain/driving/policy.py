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
    1. 기능 지원 여부(Supported Features) 체크
    2. 현재 상태 충돌(Conflict) 체크
    """
    if not current_status:
        return None

    # 지원 기능 리스트가 없으면 모든 기능 지원으로 간주 (또는 기본값 사용)
    # 클라이언트가 명시적으로 []을 보냈다면 아무것도 지원 안 함을 의미
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
            # 윈도우는 기본 기능으로 간주하지만, rear window 등은 체크 가능
            feature_name = "window" 
        elif part == "sunroof":
            feature_name = "sunroof"
        elif part == "steering_wheel":
            feature_name = "steering_wheel_heater" # 주로 열선 핸들 제어
        
        # (B) Support Check
        # 정말 기본적인 것(문, 창문 등)은 체크 생략 가능하지만, 여기선 엄격히 체크
        basic_parts = ["door_lock", "light", "wiper", "mirror", "trunk", "frunk"] 
        
        if use_support_check:
            # part가 기본 부품이 아니고, supported_features에 feature_name이 없으면 미지원
            if part not in basic_parts and feature_name not in supported_features:
                 # window는 기본으로 간주
                 if part != "window": 
                     return "feature_not_supported"

        # (C) Status Conflict Check (기존 로직 유지/확장)
        target_keys = []
        if part == "window":
            all_windows = ["window_driver", "window_passenger", "window_rear_left", "window_rear_right"]
            if loc in ["driver", "passenger", "rear_left", "rear_right"]:
                target_keys = [f"window_{loc}"]
            else:
                target_keys = [k for k in all_windows if k in current_status]

        elif part == "seat_heater":
            if loc in ["driver", "passenger"]: target_keys = [f"seat_heater_{loc}"]
            elif loc == "rear": target_keys = ["seat_heater_rear_left", "seat_heater_rear_right"]
            else: target_keys = ["seat_heater_driver"] # default
        
        elif part == "seat_ventilation":
             if loc in ["driver", "passenger"]: target_keys = [f"seat_ventilation_{loc}"]
        
        elif part == "sunroof": target_keys = ["sunroof"]
        elif part == "steering_wheel": target_keys = ["steering_wheel_heat"]
        
        elif part == "door_lock": target_keys = ["door_lock"]
        # ... 기타 부품 생략 ...

        if target_keys:
            vals = [current_status.get(k) for k in target_keys if k in current_status]
            if not vals: 
                # 상태 키조차 없다면 미지원으로 간주할 수도 있음
                return None 

            if action == "close" and all(v == "closed" for v in vals): return "already_closed"
            elif action == "open" and all(v == "open" for v in vals): return "already_open"
            elif action == "on" and all(v == "on" for v in vals): return "already_on"
            elif action == "off" and all(v == "off" for v in vals): return "already_off"

    # 2. 공조 제어
    elif intent == "control_hvac":
        action = _norm(_get_slot_value(slots, "action"))
        seat_loc = _norm(_get_slot_value(slots, "seat_location"))
        
        # (A) Support Check
        if use_support_check:
            if seat_loc == "rear" and "hvac_rear" not in supported_features:
                return "feature_not_supported"
            if seat_loc == "passenger" and "hvac_passenger" not in supported_features:
                # 듀얼 공조 미지원 시
                return "feature_not_supported"

        # (B) Conflict Check
        check_key = "hvac_power"
        current_power = current_status.get(check_key)
        
        if action == "on" and current_power == "on": return "hvac_already_on"
        if action == "off" and current_power == "off": return "hvac_already_off"

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