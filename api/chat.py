# api/chat.py
from __future__ import annotations

import uuid
import time
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from models.api_models import ChatRequest, ChatResponse
from session.session_manager import SessionManager
from nlu.router import pick_candidates
from nlu.llm_client import nlu_with_llm
from nlu.validator import validate_and_build_action
from nlu.normalizer import apply_session_rules
from utils.logging import log_event
from utils.trace_utils import state_summary, nlu_diff_hint

from nlu.executor import maybe_execute_llm_task  # ✅ 여기로 통일

router = APIRouter()
sessions = SessionManager()

SENSITIVE_META_KEYS = {
    "access_token", "authorization", "api_key", "openai_api_key",
    "password", "secret", "cookie",
}

def _mask_meta(meta: Any) -> Dict[str, Any]:
    if meta is None:
        return {}
    try:
        d = meta.model_dump()
    except Exception:
        d = dict(meta) if isinstance(meta, dict) else {"_meta": str(meta)}

    out: Dict[str, Any] = {}
    for k, v in d.items():
        if str(k).lower() in SENSITIVE_META_KEYS:
            out[k] = "***"
        else:
            out[k] = v
    return out

def _safe_action_summary(action: Any) -> Dict[str, Any]:
    if not isinstance(action, dict):
        return {"_action": str(action)}
    reply = action.get("reply") if isinstance(action.get("reply"), dict) else {}
    return {
        "reply_action_type": reply.get("action_type"),
        "reply_text_len": len(reply.get("text") or "") if isinstance(reply.get("text"), str) else None,
        "has_llm_task": bool(reply.get("llm_task")),
        "ui_hints_keys": list((reply.get("ui_hints") or {}).keys())
        if isinstance(reply.get("ui_hints"), dict) else None,
    }

@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    trace_id = uuid.uuid4().hex[:12]
    t0 = time.perf_counter()

    user_message = (req.user_message or "")
    meta = getattr(req, "meta", None)
    client_session_id = getattr(meta, "client_session_id", None)

    log_event(trace_id, "request_in", {
        "client_session_id": client_session_id,
        "mode": getattr(meta, "mode", None),
        "user_message_len": len(user_message),
        "user_message_preview": user_message[:200],
        "meta": _mask_meta(meta),
    })

    stage = "start"
    state: Any = None
    new_state: Any = None
    action: Any = None

    try:
        stage = "state_loaded"
        state = sessions.get(client_session_id, trace_id=trace_id)
        log_event(trace_id, "state_loaded", {"state_summary": state_summary(state)})

        stage = "candidates_picked"
        candidates = pick_candidates(req, state)
        log_event(trace_id, "candidates_picked", {"count": len(candidates), "candidates": candidates})

        stage = "nlu_raw"
        nlu = nlu_with_llm(req, state, candidates, trace_id=trace_id)
        log_event(trace_id, "nlu_raw", {"nlu": nlu})

        stage = "nlu_normalized"
        nlu2 = apply_session_rules(state, nlu, user_message, trace_id=trace_id)
        log_event(trace_id, "nlu_normalized", {
            "nlu": nlu2,
            "diff_hint": nlu_diff_hint(nlu or {}, nlu2 or {}),
        })

        # 🔧 FIX: validator 호출을 키워드 인자로 변경
        stage = "validation_result"
        action, new_state = validate_and_build_action(
            domain=nlu2.get("domain"),
            intent=nlu2.get("intent"),
            slots=nlu2.get("slots") or {},
            meta=meta.model_dump() if meta is not None else {},
            state=state,
            trace_id=trace_id,
        )

        # ✅ llm_task 있으면 여기서 실행
        if isinstance(action, dict) and isinstance(action.get("reply"), dict) and isinstance(action.get("plan"), dict):
            try:
                meta_dict = meta.model_dump() if meta is not None else {}
            except Exception:
                meta_dict = dict(meta) if isinstance(meta, dict) else {}
            action["reply"] = maybe_execute_llm_task(
                reply=action["reply"],
                plan=action["plan"],
                meta=meta_dict,
                trace_id=trace_id,
            )

        log_event(trace_id, "validation_result", {
            "action_summary": _safe_action_summary(action),
            "new_state_summary": state_summary(new_state),
        })

        stage = "state_saved"
        sessions.set(client_session_id, new_state, trace_id=trace_id)
        log_event(trace_id, "state_saved", {
            "client_session_id": client_session_id,
            "turn_index": new_state.get("turn_index"),
        })

        stage = "response_out"
        duration_ms = int((time.perf_counter() - t0) * 1000)
        reply = action.get("reply") if isinstance(action, dict) else None
        log_event(trace_id, "response_out", {
            "reply_preview": reply.get("text") if isinstance(reply, dict) else None,
            "duration_ms": duration_ms,
        })

        return ChatResponse(trace_id=trace_id, reply=action["reply"], state=new_state)

    except Exception as e:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        log_event(trace_id, "error", {
            "stage": stage,
            "duration_ms": duration_ms,
            "error": type(e).__name__,
            "message": str(e),
        })
        raise HTTPException(status_code=500, detail={"message": "internal_error", "trace_id": trace_id})

    finally:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        log_event(trace_id, "request_done", {
            "stage": stage,
            "duration_ms": duration_ms,
            "client_session_id": client_session_id,
        })
