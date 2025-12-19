from typing import Dict, Any
from models.api_models import ChatRequest
from domain.kiosk.schema import KIOSK_SCHEMA
from domain.kiosk.verticals.loader import load_vertical

def pick_candidates(req: ChatRequest, state: Dict[str, Any]) -> Dict[str, Any]:
    mode = (req.meta.mode or "").lower()

    if mode == "kiosk":
        kiosk_type = (req.meta.kiosk_type or "etc").lower()
        vertical = load_vertical(kiosk_type)

        intents = list(KIOSK_SCHEMA["intents"].keys())

        return {
            "domain": "kiosk",
            "kiosk_type": kiosk_type,
            "intents": intents,
            "slot_schema": KIOSK_SCHEMA["slots"],
            "vertical": vertical,  # ✅ overlay 포함
        }

    return {"domain": "general", "intents": ["smalltalk", "faq", "fallback"], "slot_schema": {}, "vertical": {}}
