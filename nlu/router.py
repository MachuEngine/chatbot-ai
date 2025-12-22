from domain import SCHEMAS

def pick_candidates(req, state):
    mode = (req.meta.mode or "").lower().strip()
    schema = SCHEMAS.get(mode)

    if not schema:
        return {
            "domain": "general",
            "intents": ["smalltalk", "faq", "fallback"],
            "slot_schema": {},
        }

    return {
        "domain": mode,
        "intents": list(schema["intents"].keys()),
        "intent_specs": schema["intents"],
        "slot_schema": schema["slots"],
    }