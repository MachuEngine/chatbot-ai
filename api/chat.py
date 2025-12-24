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
from nlu.executor import execute_action
from utils.logging import log_event
from utils.trace_utils import state_summary, nlu_diff_hint

router = APIRouter()
sessions = SessionManager()

SENSITIVE_META_KEYS = {
    "access_token",
    "authorization",
    "api_key",
    "openai_api_key",
    "password",
    "secret",
    "cookie",
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
        lk = str(k).lower()
        if lk in SENSITIVE_META_KEYS:
            out[k] = "***"
            continue
        if isinstance(v, str) and len(v) > 200:
            out[k] = v[:200] + "...(truncated)"
        elif isinstance(v, list) and len(v) > 50:
            out[k] = v[:50] + ["...(truncated)"]
        else:
            out[k] = v
    return out

def _safe_action_summary(action: Any) -> Dict[str, Any]:
    if not isinstance(action, dict):
        return {"_action": str(action)}
    reply = action.get("reply") or {}
    if not isinstance(reply, dict):
        reply = {}
    return {
        "reply_action_type": reply.get("action_type"),
        "reply_text": reply.get("text"),
        "ui_hints_keys": list((reply.get("ui_hints") or {}).keys()) if isinstance(reply.get("ui_hints"), dict) else None,
        "has_payload": "payload" in reply,
        "has_plan": "plan" in action,
        "has_result": "result" in action,
    }

def _exc_info(e: Exception) -> Dict[str, Any]:
    return {"error_type": type(e).__name__, "error_message": str(e)}

@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    trace_id = uuid.uuid4().hex[:12]

    import os
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    log_event(trace_id, "env_check", {
        "OPENAI_ENABLE_LLM": os.getenv("OPENAI_ENABLE_LLM"),
        "HAS_OPENAI_API_KEY": bool(api_key),
        "OPENAI_API_KEY_PREFIX": api_key[:6] if api_key else None,  # 예: "sk-pro"
        "OPENAI_API_KEY_LEN": len(api_key) if api_key else 0,
        "OPENAI_NLU_MODEL": os.getenv("OPENAI_NLU_MODEL"),
        "OPENAI_ANSWER_MODEL": os.getenv("OPENAI_ANSWER_MODEL"),
    })


    t0 = time.perf_counter()

    user_message = (req.user_message or "")
    client_session_id = getattr(req.meta, "client_session_id", None)

    log_event(trace_id, "request_in", {
        "client_session_id": client_session_id,
        "mode": getattr(req.meta, "mode", None),
        "user_message_len": len(user_message),
        "user_message_preview": user_message[:200],
        "meta": _mask_meta(req.meta),
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
        log_event(trace_id, "candidates_picked", {
            "count": len(candidates) if isinstance(candidates, list) else None,
            "candidates": candidates,
        })

        stage = "nlu_raw"
        nlu = nlu_with_llm(req, state, candidates, trace_id=trace_id)
        log_event(trace_id, "nlu_raw", {"nlu": nlu})

        stage = "nlu_normalized"
        nlu2 = apply_session_rules(state, nlu, user_message, trace_id=trace_id)
        log_event(trace_id, "nlu_normalized", {
            "nlu": nlu2,
            "diff_hint": nlu_diff_hint(nlu or {}, nlu2 or {}),
        })

        stage = "validation_result"
        action, new_state = validate_and_build_action(req, state, nlu2, trace_id=trace_id)
        log_event(trace_id, "validation_result", {
            "action_summary": _safe_action_summary(action),
            "new_state_summary": state_summary(new_state),
        })

        stage = "action_executed"
        action, new_state = execute_action(req=req, state=new_state, action=action, trace_id=trace_id)
        log_event(trace_id, "action_executed", {
            "action_summary": _safe_action_summary(action),
            "new_state_summary": state_summary(new_state),
        })

        stage = "state_saved"
        sessions.set(client_session_id, new_state, trace_id=trace_id)
        log_event(trace_id, "state_saved", {
            "client_session_id": client_session_id,
            "turn_index": new_state.get("turn_index") if isinstance(new_state, dict) else None,
        })

        stage = "response_out"
        duration_ms = int((time.perf_counter() - t0) * 1000)
        reply = action.get("reply") if isinstance(action, dict) else None
        reply_preview = reply.get("text") if isinstance(reply, dict) else None

        log_event(trace_id, "response_out", {
            "reply_preview": reply_preview,
            "duration_ms": duration_ms,
        })

        return ChatResponse(trace_id=trace_id, reply=action["reply"], state=new_state)

    except HTTPException as e:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        log_event(trace_id, "error", {
            "stage": stage,
            "duration_ms": duration_ms,
            "http_status": e.status_code,
            "detail": e.detail,
            "state_summary": state_summary(state) if isinstance(state, dict) else {"_state": str(state)},
        })
        raise

    except Exception as e:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        log_event(trace_id, "error", {
            "stage": stage,
            "duration_ms": duration_ms,
            "state_summary": state_summary(state) if isinstance(state, dict) else {"_state": str(state)},
            "new_state_summary": state_summary(new_state) if isinstance(new_state, dict) else {"_state": str(new_state)},
            "action_summary": _safe_action_summary(action),
            **_exc_info(e),
        })
        raise HTTPException(status_code=500, detail={"message": "internal_error", "trace_id": trace_id})

    finally:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        log_event(trace_id, "request_done", {
            "stage": stage,
            "duration_ms": duration_ms,
            "client_session_id": client_session_id,
        })
