# session/session_manager.py
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import redis

try:
    from utils.logging import log_event  # type: ignore
except Exception:
    log_event = None  # type: ignore


class SessionManager:
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str = "chatbot:session:",
        ttl_seconds: int = 60 * 60 * 6,  # 6시간
    ):
        self.r = redis.Redis.from_url(redis_url, decode_responses=True)
        self.key_prefix = key_prefix
        self.ttl_seconds = ttl_seconds

    def _key(self, client_session_id: str) -> str:
        sid = (client_session_id or "").strip()
        if not sid:
            sid = f"anon_{int(time.time()*1000)}"
        return f"{self.key_prefix}{sid}"

    def _new_state(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "conversation_id": f"conv_{int(now*1000)}",
            "turn_index": 0,
            "history_summary": "",
            "current_domain": None,
            "active_intent": None,
            "slots": {},
            "last_bot_action": None,
            "created_at": now,
            "updated_at": now,
        }

    def get(self, client_session_id: str, trace_id: Optional[str] = None) -> Dict[str, Any]:
        k = self._key(client_session_id)
        raw = self.r.get(k)

        if not raw:
            state = self._new_state()
            self.r.set(k, json.dumps(state, ensure_ascii=False))
            self.r.expire(k, self.ttl_seconds)

            if log_event and trace_id:
                log_event(trace_id, "state_created", {
                    "client_session_id": client_session_id,
                    "conversation_id": state.get("conversation_id"),
                    "ttl_seconds": self.ttl_seconds,
                })
            return state

        state = json.loads(raw)
        # TTL 갱신(슬라이딩 세션)
        self.r.expire(k, self.ttl_seconds)

        if log_event and trace_id:
            log_event(trace_id, "state_loaded", {
                "client_session_id": client_session_id,
                "conversation_id": state.get("conversation_id"),
                "turn_index": state.get("turn_index"),
                "ttl_seconds": self.ttl_seconds,
            })
        return state

    def set(self, client_session_id: str, state: Dict[str, Any], trace_id: Optional[str] = None) -> None:
        k = self._key(client_session_id)
        st = dict(state or {})
        st["updated_at"] = time.time()
        if "created_at" not in st:
            st["created_at"] = st["updated_at"]
        if st.get("slots") is None:
            st["slots"] = {}

        self.r.set(k, json.dumps(st, ensure_ascii=False))
        self.r.expire(k, self.ttl_seconds)

        if log_event and trace_id:
            log_event(trace_id, "state_saved", {
                "client_session_id": client_session_id,
                "conversation_id": st.get("conversation_id"),
                "turn_index": st.get("turn_index"),
                "ttl_seconds": self.ttl_seconds,
            })
