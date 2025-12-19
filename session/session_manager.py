from typing import Dict, Any
import time

class SessionManager:
    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}

    def get(self, client_session_id: str) -> Dict[str, Any]:
        if client_session_id not in self._store:
            self._store[client_session_id] = {
                "conversation_id": f"conv_{int(time.time()*1000)}",
                "turn_index": 0,
                "history_summary": "",
                "current_domain": None,
                "active_intent": None,
                "slots": {},
                "last_bot_action": None,
            }
        return self._store[client_session_id]

    def set(self, client_session_id: str, state: Dict[str, Any]) -> None:
        self._store[client_session_id] = state
