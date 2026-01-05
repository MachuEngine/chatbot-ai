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

    # 지원 기능 리스트가 없으면(None) 모든 기능 지원으로 간주
    # 빈 리스트([])라면 아무것도 지원 안 함을 의미
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
        # 기본 부품은 체크 면제 (정책에 따라 변경 가능)
        basic_parts = ["door_lock", "light", "wiper", "mirror", "trunk", "frunk"] 
        
        if use_support_check:
            if part not in basic_parts and feature_name not in supported_features:
                 if part != "window": 
                     return "feature_not_supported"

        # (C) Status Conflict Check
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
            else: target_keys = ["seat_heater_driver"]
        
        elif part == "seat_ventilation":
             if loc in ["driver", "passenger"]: target_keys = [f"seat_ventilation_{loc}"]
        
        elif part == "sunroof": target_keys = ["sunroof"]
        elif part == "steering_wheel": target_keys = ["steering_wheel_heat"]
        elif part == "door_lock": target_keys = ["door_lock"]

        if target_keys:
            vals = [current_status.get(k) for k in target_keys if k in current_status]
            if not vals: 
                return None 

            if action == "close" and all(v == "closed" for v in vals): return "already_closed"
            elif action == "open" and all(v == "open" for v in vals): return "already_open"
            elif action == "on" and all(v == "on" for v in vals): return "already_on"
            elif action == "off" and all(v == "off" for v in vals): return "already_off"

    # 2. 공조 제어
    elif intent == "control_hvac":
        action = _norm(_get_slot_value(slots, "action"))
        seat_loc = _norm(_get_slot_value(slots, "seat_location"))
        
        # [수정] (A) Support Check 강화 (메인 공조 포함)
        if use_support_check:
            # 뒷좌석 공조 요청 시
            if seat_loc == "rear" and "hvac_rear" not in supported_features:
                return "feature_not_supported"
            # 조수석 공조 요청 시
            if seat_loc == "passenger" and "hvac_passenger" not in supported_features:
                return "feature_not_supported"
            
            # [추가] 메인 공조(운전석/전체) 요청 시에도 체크 필요
            # 시뮬레이터에서 'hvac_passenger'나 'hvac_rear' 외에 'hvac_main' 같은 키를 보내지 않는다면,
            # 최소한 hvac 관련 기능이 하나라도 있는지 확인하거나,
            # 별도의 'hvac_power' 피쳐를 정의해야 함.
            # 여기서는 'hvac_passenger'가 있으면 메인도 있다고 가정하거나, 
            # 엄격하게 'hvac_main'이 없으면 안 된다고 할 수 있음.
            # 현재 시뮬레이터는 hvac_passenger/rear만 체크박스가 있음 -> 이를 보완해야 함.
            
            # 임시 해결: hvac_passenger가 있으면 메인도 있다고 가정.
            # 하지만 더 좋은 건 스키마에 hvac_main을 추가하는 것임.
            # 만약 passenger/rear 둘 다 없다면? -> 깡통차? -> 미지원 처리
            if not seat_loc or seat_loc in ["driver", "all"]:
                 # 메인 공조를 켜려고 하는데, hvac 관련 피쳐가 하나도 없다면?
                 # (시뮬레이터에서 hvac_passenger 체크박스가 꺼져있으면 메인도 없다고 간주할 것인가?)
                 # 안전하게: supported_features 리스트에 'hvac' 관련 키워드가 하나라도 있는지 확인
                 has_hvac = any("hvac" in f for f in supported_features)
                 if not has_hvac:
                     return "feature_not_supported"

        # (B) Conflict Check
        check_key = "hvac_power"
        current_power = current_status.get(check_key)
        
        if action == "off" and current_power == "off": return "hvac_already_off"

    return None


def build_vehicle_command(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
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