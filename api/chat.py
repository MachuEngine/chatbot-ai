from __future__ import annotations

import uuid
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from models.api_models import ChatRequest, ChatResponse
from session.session_manager import SessionManager
from nlu.router import pick_candidates
from nlu.llm_client import nlu_with_llm
from nlu.validator import validate_and_build_action
from nlu.normalizer import apply_session_rules
from nlu.edu_answer_llm import generate_edu_answer_with_llm
from nlu.edu_guard import is_edu_relevant
from utils.logging import log_event
from utils.trace_utils import state_summary, nlu_diff_hint
# ✅ [변경] Repository 생성을 위한 팩토리 함수를 import합니다.
from domain.kiosk.policy import default_catalog_repo

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

EDU_PAYLOAD_KEYS = {"content", "student_answer", "topic"}


def _mask_meta(meta: Any) -> Dict[str, Any]:
    if meta is None:
        return {}
    try:
        d = meta.model_dump()
    except Exception:
        d = dict(meta) if isinstance(meta, dict) else {"_meta": str(meta)}

    for k in list(d.keys()):
        if k in EDU_PAYLOAD_KEYS:
            d.pop(k, None)

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
        "ui_hints_keys": list((reply.get("ui_hints") or {}).keys())
        if isinstance(reply.get("ui_hints"), dict)
        else None,
        "has_payload": "payload" in reply,
        "has_llm_task": "llm_task" in action,
    }


def _exc_info(e: Exception) -> Dict[str, Any]:
    return {"error_type": type(e).__name__, "error_message": str(e)}


def _safe_meta_for_validator(meta: Any) -> Dict[str, Any]:
    """
    validator는 Dict meta를 기대하므로 pydantic -> dict 변환
    """
    if meta is None:
        return {}
    if hasattr(meta, "model_dump"):
        try:
            d = meta.model_dump()
        except Exception:
            return {}
    else:
        d = meta if isinstance(meta, dict) else {}

    for k in list(d.keys()):
        if k in EDU_PAYLOAD_KEYS:
            d.pop(k, None)
    return d


def _merge_edu_payload_from_req_and_meta(req: ChatRequest) -> Dict[str, Optional[str]]:
    meta_dump: Dict[str, Any] = {}
    try:
        meta_dump = req.meta.model_dump()
    except Exception:
        meta_dump = {}

    content = req.content or meta_dump.get("content")
    student_answer = req.student_answer or meta_dump.get("student_answer")
    topic = req.topic or meta_dump.get("topic")

    req.content = content
    req.student_answer = student_answer
    req.topic = topic

    return {
        "content": content,
        "student_answer": student_answer,
        "topic": topic,
    }


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    trace_id = uuid.uuid4().hex[:12]
    t0 = time.perf_counter()

    user_message = (req.user_message or "")
    client_session_id = getattr(req.meta, "client_session_id", None)

    edu_payload = _merge_edu_payload_from_req_and_meta(req)

    log_event(
        trace_id,
        "request_in",
        {
            "client_session_id": client_session_id,
            "mode": getattr(req.meta, "mode", None),
            "user_message_len": len(user_message),
            "user_message_preview": user_message[:200],
            "meta": _mask_meta(req.meta),
            "edu_payload_keys": [k for k, v in edu_payload.items() if v is not None],
        },
    )

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
        log_event(
            trace_id,
            "candidates_picked",
            {
                "count": len(candidates) if isinstance(candidates, list) else None,
                "candidates": candidates,
            },
        )

        stage = "nlu_raw"
        nlu = nlu_with_llm(req, state, candidates, trace_id=trace_id)
        log_event(trace_id, "nlu_raw", {"nlu": nlu})

        stage = "nlu_normalized"
        nlu2 = apply_session_rules(state, nlu, user_message, trace_id=trace_id)
        log_event(
            trace_id,
            "nlu_normalized",
            {
                "nlu": nlu2,
                "diff_hint": nlu_diff_hint(nlu or {}, nlu2 or {}),
            },
        )

        stage = "validation_result"
        meta_dict = _safe_meta_for_validator(req.meta)

        # ✅ [변경] Repo 객체를 생성합니다.
        # (실무에서는 DB Connection Pool이나 DI Container를 통해 주입받는 것이 좋습니다)
        catalog_repo = default_catalog_repo()

        # ✅ [변경] 생성된 Repo를 Validator에 전달합니다.
        action, new_state = validate_and_build_action(
            domain=(nlu2.get("domain") or "").strip(),
            intent=(nlu2.get("intent") or "").strip(),
            slots=(nlu2.get("slots") or {}) if isinstance(nlu2.get("slots"), dict) else {},
            meta=meta_dict,
            state=state if isinstance(state, dict) else {},
            trace_id=trace_id,
            catalog=catalog_repo,  # DI Injection
        )

        log_event(
            trace_id,
            "validation_result",
            {
                "action_summary": _safe_action_summary(action),
                "new_state_summary": state_summary(new_state),
            },
        )

        stage = "llm_task_execute"
        if isinstance(action, dict) and isinstance(action.get("llm_task"), dict):
            llm_task = action["llm_task"]

            if (nlu2.get("domain") == "education") and llm_task.get("type") == "edu_answer_generation":
                ok, why = is_edu_relevant(user_message)
                if not ok:
                    reply = action.get("reply") if isinstance(action.get("reply"), dict) else {}
                    reply["text"] = (
                        "지금은 한국어 학습(발음/문법/작문/요약/첨삭 등) 관련 질문만 도와줄 수 있어요.\n"
                        "예) '연음이 뭐야?', '이 문장 맞춤법 고쳐줘', '다음 글 요약해줘'"
                    )
                    action["reply"] = reply
                    action.pop("llm_task", None)
                    log_event(trace_id, "edu_guard_blocked", {"reason": why})

                else:
                    generated = generate_edu_answer_with_llm(
                        task_input=llm_task.get("input") or {},
                        user_message=user_message,
                        trace_id=trace_id,
                    )

                    reply = action.get("reply") if isinstance(action.get("reply"), dict) else {}
                    reply["text"] = (generated.get("text") or "").strip() or reply.get("text") or "처리할게요."

                    if isinstance(generated.get("ui_hints"), dict):
                        base = reply.get("ui_hints") if isinstance(reply.get("ui_hints"), dict) else {}
                        reply["ui_hints"] = {**base, **generated["ui_hints"]}

                    action["reply"] = reply
                    action.pop("llm_task", None)

                    log_event(
                        trace_id,
                        "llm_task_execute_ok",
                        {"type": "edu_answer_generation", "reply_text_len": len(reply.get("text") or "")},
                    )

        stage = "state_saved"
        sessions.set(client_session_id, new_state, trace_id=trace_id)
        log_event(
            trace_id,
            "state_saved",
            {
                "client_session_id": client_session_id,
                "turn_index": new_state.get("turn_index") if isinstance(new_state, dict) else None,
            },
        )

        stage = "response_out"
        duration_ms = int((time.perf_counter() - t0) * 1000)
        reply = action.get("reply") if isinstance(action, dict) else None
        reply_preview = reply.get("text") if isinstance(reply, dict) else None

        log_event(
            trace_id,
            "response_out",
            {
                "reply_preview": reply_preview,
                "duration_ms": duration_ms,
            },
        )

        return ChatResponse(trace_id=trace_id, reply=action["reply"], state=new_state)

    except HTTPException as e:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        log_event(
            trace_id,
            "error",
            {
                "stage": stage,
                "duration_ms": duration_ms,
                "http_status": e.status_code,
                "detail": e.detail,
                "state_summary": state_summary(state) if isinstance(state, dict) else {"_state": str(state)},
            },
        )
        raise

    except Exception as e:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        log_event(
            trace_id,
            "error",
            {
                "stage": stage,
                "duration_ms": duration_ms,
                "state_summary": state_summary(state) if isinstance(state, dict) else {"_state": str(state)},
                "new_state_summary": state_summary(new_state) if isinstance(new_state, dict) else {"_state": str(new_state)},
                "action_summary": _safe_action_summary(action),
                **_exc_info(e),
            },
        )
        raise HTTPException(status_code=500, detail={"message": "internal_error", "trace_id": trace_id})

    finally:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        log_event(
            trace_id,
            "request_done",
            {
                "stage": stage,
                "duration_ms": duration_ms,
                "client_session_id": client_session_id,
            },
        )