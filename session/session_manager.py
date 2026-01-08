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

    def _key(self, platform_id: str, user_id: str) -> str:
        """
        Redis Key: chatbot:session:{platform_id}:{user_id}
        """
        pid = (platform_id or "default").strip()
        uid = (user_id or "").strip()
        
        if not uid:
            uid = f"anon_{int(time.time()*1000)}"
            
        return f"{self.key_prefix}{pid}:{uid}"

    def _new_state(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "conversation_id": f"conv_{int(now*1000)}",
            "turn_index": 0,
            "history_summary": "",
            "history": [],
            "current_domain": None,
            "active_intent": None,
            "slots": {},
            # [Added] Companion 설정을 저장할 필드들
            "persona": None,
            "tone_style": None, # (Legacy or Edu tone)
            "mood_preset": "cheerful",
            "topic_hint": None,
            "verbosity": "normal",
            
            "last_bot_action": None,
            "created_at": now,
            "updated_at": now,
        }

    def get(self, platform_id: str, user_id: str, trace_id: Optional[str] = None) -> Dict[str, Any]:
        k = self._key(platform_id, user_id)
        raw = self.r.get(k)

        if not raw:
            state = self._new_state()
            self.r.set(k, json.dumps(state, ensure_ascii=False))
            self.r.expire(k, self.ttl_seconds)

            if log_event and trace_id:
                log_event(trace_id, "state_created", {
                    "platform_id": platform_id,
                    "user_id": user_id,
                    "redis_key": k,
                    "conversation_id": state.get("conversation_id"),
                })
            return state

        state = json.loads(raw)
        self.r.expire(k, self.ttl_seconds)

        if log_event and trace_id:
            log_event(trace_id, "state_loaded", {
                "platform_id": platform_id,
                "user_id": user_id,
                "conversation_id": state.get("conversation_id"),
                "turn_index": state.get("turn_index"),
            })
        return state

    def set(self, platform_id: str, user_id: str, state: Dict[str, Any], trace_id: Optional[str] = None) -> None:
        k = self._key(platform_id, user_id)
        st = dict(state or {})
        st["updated_at"] = time.time()
        if "created_at" not in st:
            st["created_at"] = st["updated_at"]
        if st.get("slots") is None:
            st["slots"] = {}
        if "history" not in st:
            st["history"] = []

        self.r.set(k, json.dumps(st, ensure_ascii=False))
        self.r.expire(k, self.ttl_seconds)

        if log_event and trace_id:
            log_event(trace_id, "state_saved", {
                "platform_id": platform_id,
                "user_id": user_id,
                "conversation_id": st.get("conversation_id"),
                "turn_index": st.get("turn_index"),
                # 주요 설정값 로그 확인용
                "persona": st.get("persona"),
                "mood_preset": st.get("mood_preset")
            })

    def add_history(self, platform_id: str, user_id: str, role: str, message: str, limit: int = 30) -> None:
        state = self.get(platform_id, user_id)
        history = state.get("history", [])
        
        history.append({
            "role": role, 
            "content": message, 
            "ts": time.time()
        })
        
        if len(history) > limit:
            history = history[-limit:]
            
        state["history"] = history
        self.set(platform_id, user_id, state)