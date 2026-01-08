# api/chat.py
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
from nlu.response_renderer import render_from_result
from nlu.emotion_analyzer import analyze_user_emotion # [New Import]
from utils.logging import log_event
from utils.trace_utils import state_summary, nlu_diff_hint
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

    # Meta에서 Platform ID, User ID 추출
    meta_obj = req.meta if isinstance(req.meta, dict) else (req.meta.model_dump() if hasattr(req.meta, "model_dump") else {})
    platform_id = str(meta_obj.get("platform_id") or "web").strip()
    user_id = str(meta_obj.get("user_id") or meta_obj.get("client_session_id") or "").strip()
    current_mode = str(meta_obj.get("mode") or "").strip()

    edu_payload = _merge_edu_payload_from_req_and_meta(req)

    log_event(
        trace_id,
        "request_in",
        {
            "platform_id": platform_id,
            "user_id": user_id,
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
        state = sessions.get(platform_id, user_id, trace_id=trace_id)
        
        # ✅ [FIX] Meta(UI설정)을 Session State에 동기화
        # UI에서 넘어온 설정값이 있으면 세션에 업데이트합니다. (mood_preset 제거)
        updates = {}
        for key in ["persona", "topic_hint", "verbosity"]:
            val = meta_obj.get(key)
            if val is not None and state.get(key) != val:
                state[key] = val
                updates[key] = val
        
        # Tone Style (Legacy or Edu)
        tone_val = meta_obj.get("tone_style")
        if tone_val and state.get("tone_style") != tone_val:
            state["tone_style"] = tone_val
            updates["tone_style"] = tone_val
            
        if updates:
            log_event(trace_id, "state_update_from_meta", updates)

        # [Added] 감정 분석 및 상태 업데이트
        if user_message:
            current_emotion = state.get("user_emotion_profile", {})
            
            # [Fix] 가져온 프로필이 dict가 아니면 초기화 (오류 방지)
            if not isinstance(current_emotion, dict):
                current_emotion = {}
                
            new_emotion = analyze_user_emotion(user_message, current_emotion)
            state["user_emotion_profile"] = new_emotion
            log_event(trace_id, "emotion_analyzed", new_emotion)
        
        # 사용자 메시지를 히스토리에 추가
        if "history" not in state:
            state["history"] = []
        state["history"].append({"role": "user", "content": user_message, "ts": time.time()})
        if len(state["history"]) > 30:
            state["history"] = state["history"][-30:]

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
        
        # [NEW] NLU 슬롯에서 톤 변경 감지 -> 세션에 반영
        detected_tone = (nlu2.get("slots") or {}).get("tone_style")
        if detected_tone:
            state["tone_style"] = detected_tone
            state["persona"] = detected_tone # NLU가 감지한 톤을 페르소나에도 반영
            log_event(trace_id, "state_update_tone", {"new_tone": detected_tone})

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
        
        # Validator가 텍스트 보정을 할 수 있도록 user_message 원본을 meta_dict에 주입
        meta_dict["user_message_preview"] = user_message

        catalog_repo = default_catalog_repo()

        action, new_state = validate_and_build_action(
            domain=(nlu2.get("domain") or "").strip(),
            intent=(nlu2.get("intent") or "").strip(),
            slots=(nlu2.get("slots") or {}) if isinstance(nlu2.get("slots"), dict) else {},
            meta=meta_dict,
            state=state if isinstance(state, dict) else {},
            trace_id=trace_id,
            catalog=catalog_repo,
        )

        # [Important] State 보존: Validator가 new_state를 새로 만들 수 있으므로
        # 우리가 앞서 설정한 companion 설정값들이 유지되도록 보장 (mood_preset 제거)
        if isinstance(new_state, dict):
            # 1. 기존 state의 companion 값들을 복사
            for key in ["persona", "topic_hint", "verbosity", "tone_style"]:
                if key in state and key not in new_state:
                    new_state[key] = state[key]
            
            # 2. NLU가 감지한 톤이 있다면 우선 적용 (덮어쓰기)
            if detected_tone:
                new_state["tone_style"] = detected_tone
                new_state["persona"] = detected_tone

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
                history_list = state.get("history", [])
                generated = generate_edu_answer_with_llm(
                    task_input=llm_task.get("input") or {},
                    user_message=user_message,
                    history=history_list,
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
                    {
                        "type": "edu_answer_generation", 
                        "reply_text_len": len(reply.get("text") or "")
                    },
                )

        # ✅ [Renderer] 렌더러 호출 (Template & LLM Surface Rewrite 적용)
        if isinstance(action, dict) and "reply" in action:
            render_result_mock = {
                "ok": True,
                "facts": action["reply"].get("payload", {})
            }
            
            # [Updated] meta와 state를 전달 (state에는 tone_style 등이 포함됨)
            final_text = render_from_result(
                reply=action["reply"],
                plan=nlu2,
                result=render_result_mock,
                trace_id=trace_id,
                meta=req.meta,
                state=new_state if new_state else state
            )
            
            if final_text and final_text.strip():
                action["reply"]["text"] = final_text

        # 봇의 응답을 히스토리에 추가
        reply_text = ""
        if isinstance(action, dict) and isinstance(action.get("reply"), dict):
            reply_text = action["reply"].get("text", "")
        
        if reply_text:
            if "history" not in new_state:
                new_state["history"] = state.get("history", [])[:]
            
            new_state["history"].append({"role": "assistant", "content": reply_text, "ts": time.time()})
            if len(new_state["history"]) > 30:
                new_state["history"] = new_state["history"][-30:]

        stage = "state_saved"
        sessions.set(platform_id, user_id, new_state, trace_id=trace_id)

        log_event(
            trace_id,
            "state_saved",
            {
                "platform_id": platform_id,
                "user_id": user_id,
                "turn_index": new_state.get("turn_index") if isinstance(new_state, dict) else None,
                "persona": new_state.get("persona"), # 로그 확인용
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
                "platform_id": platform_id,
                "user_id": user_id,
            },
        )