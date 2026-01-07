# domain/companion/policy.py
from typing import Any, Dict, Optional, List

def check_action_validity(
    intent: str, 
    slots: Dict[str, Any], 
    current_status: Dict[str, Any],
    supported_features: Optional[List[str]] = None
) -> Optional[str]:
    # Companion 모드는 기본적으로 모든 대화를 허용
    return None

def build_vehicle_command(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    # NLU가 파악한 의도를 실행 가능한 커맨드로 변환
    return {
        "type": "companion_chat",
        "params": {
            "intent": intent,
            "slots": slots
        }
    }