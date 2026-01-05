# domain/driving/state_manager.py
from typing import Any, Dict

class VehicleStateManager:
    """차량의 센서 데이터(Meta)와 대화 기억(Saved State)을 동기화하고 관리합니다."""
    
    # UI/NLU 키와 하드웨어 상태 키 간의 매핑 정의
    PART_KEY_MAP = {
        "steering_wheel": "steering_wheel_heat",
        "seat_heater": "seat_heater_driver", # 기본값
        "light": "light_head",
        "wiper": "wiper_front"
    }

    @staticmethod
    def sync_status(saved_status: Dict[str, Any], meta_status: Dict[str, Any]) -> Dict[str, Any]:
        """
        현실(Meta)을 우선하되 기억(Saved)의 세부 정보를 보존합니다.
        """
        synced = dict(saved_status or {})
        # 메타 정보(실제 센서/UI 값)가 최우선 순위입니다.
        if meta_status:
            synced.update(meta_status)
        return synced

    @classmethod
    def simulate_action(cls, current_status: Dict[str, Any], part: str, action: str, detail: str = "") -> Dict[str, Any]:
        """액션 수행 후의 예상 상태를 시뮬레이션합니다."""
        new_status = dict(current_status)
        
        # Action to Value mapping
        val_map = {
            "open": "open", "close": "closed",
            "on": "on", "off": "off",
            "lock": "locked", "unlock": "unlocked"
        }
        val = val_map.get(action.lower())
        if not val:
            return new_status

        # 1. 매핑된 키 확인
        target_key = cls.PART_KEY_MAP.get(part, part)
        
        # 2. 예외 케이스 처리 (Window, Seat 등)
        if part == "window":
            for k in ["window_driver", "window_passenger", "window_rear_left", "window_rear_right"]:
                new_status[k] = val
        elif part == "seat_heater":
            if "rear" in detail:
                new_status["seat_heater_rear_left"] = val
                new_status["seat_heater_rear_right"] = val
            else:
                new_status["seat_heater_driver"] = val
        else:
            new_status[target_key] = val
            
        return new_status