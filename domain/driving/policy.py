# domain/driving/policy.py
from typing import Any, Dict, Optional, List

def _norm(val: Any) -> str:
    if not isinstance(val, str): return ""
    return val.lower().strip()

def check_action_validity(intent: str, slots: Dict[str, Any], current_status: Dict[str, Any]) -> Optional[str]:
    """
    명령 수행 전, 현재 상태와 비교하여 불필요한 행동(Conflict)인지 체크
    """
    if not current_status:
        return None

    def _val(k):
        v = slots.get(k)
        if isinstance(v, dict): return v.get("value")
        return v

    # 1. 하드웨어 제어
    if intent == "control_hardware":
        part = _norm(_val("target_part"))      # window
        action = _norm(_val("action"))         # close
        loc = _norm(_val("location_detail"))   # all / driver / null

        # [수정] 부품별 관련 상태 키 매핑
        # 차량이 보내주는 실제 status key들과 매칭되어야 함
        target_keys = []
        
        if part == "window":
            all_windows = ["window_driver", "window_passenger", "window_rear_left", "window_rear_right"]
            # location에 따라 필터링
            if loc in ["driver", "passenger", "rear_left", "rear_right"]:
                target_keys = [f"window_{loc}"]
            else:
                # loc가 all이거나 없으면 -> 현재 status에 존재하는 모든 window 키 확인
                target_keys = [k for k in all_windows if k in current_status]
        
        elif part == "seat_heater":
            target_keys = [f"seat_heater_{loc}"] if loc else ["seat_heater_driver"]
        
        elif part == "trunk":
            target_keys = ["trunk"]
        
        elif part == "door_lock":
            target_keys = ["door_lock"]

        if not target_keys:
            return None # 상태 정보가 없으면 제어 허용

        # [중요] "전체" 대상일 때의 충돌 판단 로직
        # 예: "창문 닫아" -> "모든" 창문이 이미 closed여야 conflict
        # 하나라도 open이면 동작 수행(valid)
        
        vals = [current_status.get(k) for k in target_keys if k in current_status]
        if not vals: 
            return None

        # 상태 비교
        if action == "close":
            # 모든 대상이 이미 closed여야 "이미 닫혀있다"고 말함
            if all(v == "closed" for v in vals): return "already_closed"
        
        elif action == "open":
            if all(v == "open" for v in vals): return "already_open"
            
        elif action == "on":
            if all(v == "on" for v in vals): return "already_on"
            
        elif action == "off":
            if all(v == "off" for v in vals): return "already_off"
            
        elif action == "lock":
            if all(v == "locked" for v in vals): return "already_locked"
            
        elif action == "unlock":
            if all(v == "unlocked" for v in vals): return "already_unlocked"

    # 2. 공조 제어
    elif intent == "control_hvac":
        action = _norm(_val("action"))
        current_power = current_status.get("hvac_power")

        if action == "on" and current_power == "on":
            return "hvac_already_on"
        if action == "off" and current_power == "off":
            return "hvac_already_off"

    return None


def build_vehicle_command(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    """
    NLU 슬롯 결과를 차량 제어용 JSON 커맨드로 변환
    """
    def _val(k):
        v = slots.get(k)
        if isinstance(v, dict): return v.get("value")
        return v

    command = {"type": "none", "params": {}}

    if intent == "control_hvac":
        command["type"] = "hvac_control"
        command["params"] = {
            "action": _norm(_val("action")) or "on",
            "target_temp": _val("target_temp"),
            "seat_location": _val("seat_location"),
            "fan_speed": _val("fan_speed"),
        }

    elif intent == "control_hardware":
        command["type"] = "hardware_control"
        command["params"] = {
            "part": _norm(_val("target_part")),
            "action": _norm(_val("action")),
            "location_detail": _val("location_detail"),
        }

    elif intent == "navigate_to":
        command["type"] = "navigation"
        command["params"] = {
            "destination": _val("destination"),
            "waypoint": _val("waypoint"),
        }

    elif intent == "find_poi":
        command["type"] = "search_poi"
        command["params"] = {
            "poi_type": _val("poi_type"),
            "sort_by": _val("sort_by"),
        }
    
    return command