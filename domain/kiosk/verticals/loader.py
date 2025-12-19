# domain/kiosk/verticals/loader.py
from typing import Dict, Any
from domain.kiosk.verticals.cafe import KIOSK_VERTICAL_CAFE

_REGISTRY = {
    "cafe": KIOSK_VERTICAL_CAFE,
    # "cinema": KIOSK_VERTICAL_CINEMA,  # 나중에 추가
    # "hospital": ...
}

def load_vertical(kiosk_type: str | None) -> Dict[str, Any]:
    if not kiosk_type:
        return {}
    return _REGISTRY.get(kiosk_type.lower(), {})
