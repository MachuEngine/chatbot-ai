# nlu/executor.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from utils.logging import log_event
from nlu.messages import TEMPLATES

try:
    # 네 프로젝트에서 answer 생성 담당 모듈명이 다를 수 있음
    # (예: nlu.llm_answer_client / nlu.response_renderer 등)
    from nlu.llm_answer_client import generate_text_with_llm  # type: ignore
except Exception:
    generate_text_with_llm = None  # type: ignore


def _safe_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _format_template(key: str, vars: Optional[Dict[str, Any]] = None) -> str:
    tmpl = TEMPLATES.get(key) or TEMPLATES.get("result.fail.generic") or "처리를 완료하지 못했어요. 잠시 후 다시 시도해 주세요."
    try:
        return tmpl.format(**(vars or {}))
    except Exception:
        return tmpl


def _set_reply_text(action: Dict[str, Any], text: str) -> Dict[str, Any]:
    reply = _safe_dict(action.get("reply"))
    reply["text"] = text
    action["reply"] = reply
    return action


def execute_action(
    req: Any,
    state: Dict[str, Any],
    action: Dict[str, Any],
    trace_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    action(reply.llm_task)가 있으면 LLM로 text 생성.
    없으면 message_key_ok 템플릿을 그대로 렌더링(혹은 기존 로직 유지).
    """

    st = _safe_dict(state)
    act = _safe_dict(action)
    reply = _safe_dict(act.get("reply"))
    plan = _safe_dict(act.get("plan"))

    domain = str((plan.get("domain") or reply.get("ui_hints", {}).get("domain") or st.get("current_domain") or "")).strip()
    intent = str((plan.get("intent") or reply.get("ui_hints", {}).get("intent") or st.get("active_intent") or "")).strip()

    llm_task = reply.get("llm_task")
    ok_key = str(reply.get("message_key_ok") or "fallback.mvp")
    fail_key = str(reply.get("message_key_fail") or "result.fail.generic")

    # ✅ Education 상태 꼬임 방지: question은 최신 user_message로 덮기
    # (explain_concept처럼 question 슬롯이 NLU에 없을 때 이전 값이 남는 문제 방지)
    try:
        user_message = (req.user_message or "").strip()
    except Exception:
        user_message = ""

    if domain == "education" and user_message:
        slots = st.get("slots")
        if not isinstance(slots, dict):
            slots = {}
        slots["question"] = {"value": user_message, "confidence": 0.8}
        st["slots"] = slots

    # 1) LLM task가 있으면: 무조건 생성 시도
    if isinstance(llm_task, dict) and llm_task.get("kind"):
        if generate_text_with_llm is None:
            # 코드가 아직 import 안되면 명확히 fail
            text = _format_template(fail_key)
            act = _set_reply_text(act, text)
            act["result"] = {"ok": False, "error": "llm_answer_client_not_loaded"}
            log_event(trace_id, "executor_llm_missing_impl", {"domain": domain, "intent": intent})
            log_event(trace_id, "executor_done", {"domain": domain, "intent": intent, "ok": True})
            return act, st

        try:
            # llm_task에는 {kind, slots} 형태로 들어옴
            kind = str(llm_task.get("kind"))
            slots_in = llm_task.get("slots") or {}
            slots_in = slots_in if isinstance(slots_in, dict) else {}

            text = generate_text_with_llm(
                kind=kind,
                slots=slots_in,
                trace_id=trace_id,
            )

            if not isinstance(text, str) or not text.strip():
                raise RuntimeError("empty_llm_text")

            act = _set_reply_text(act, text)
            act["result"] = {"ok": True}
            log_event(trace_id, "executor_llm_ok", {"domain": domain, "intent": intent, "kind": kind, "text_len": len(text)})

        except Exception as e:
            # 생성 실패 시 fail 템플릿로
            text = _format_template(fail_key)
            act = _set_reply_text(act, text)
            act["result"] = {"ok": False, "error": type(e).__name__, "message": str(e)[:200]}
            log_event(trace_id, "executor_llm_fail", {"domain": domain, "intent": intent, "error": type(e).__name__, "message": str(e)[:200]})

        log_event(trace_id, "executor_done", {"domain": domain, "intent": intent, "ok": True})
        return act, st

    # 2) LLM task가 없으면: 기존 템플릿 기반
    text = _format_template(ok_key)
    act = _set_reply_text(act, text)
    act["result"] = {"ok": True}
    log_event(trace_id, "executor_done", {"domain": domain, "intent": intent, "ok": True})
    return act, st
