# nlu/response_renderer.py
from __future__ import annotations

from typing import Any, Dict, Optional

from nlu.messages import TEMPLATES
from nlu.llm_surface_client import surface_rewrite
from nlu.llm_answer_client import generate_education_answer, generate_education_summary


def _format_template(key: str, vars: Dict[str, Any]) -> str:
    tmpl = TEMPLATES.get(key) or TEMPLATES.get("fallback.mvp") or ""
    try:
        return tmpl.format(**(vars or {}))
    except Exception:
        return tmpl


def _surface_enabled(message_key: str) -> bool:
    # [확인] driving 도메인 허용
    if message_key.startswith("result.kiosk."):
        return True
    if message_key.startswith("result.driving."):
        return True
    return False


def _options_text(facts: Dict[str, Any]) -> str:
    og = facts.get("option_groups")
    if not isinstance(og, dict) or not og:
        return ""
    pairs = [f"{k}={v}" for k, v in og.items() if v is not None and str(v).strip() != ""]
    return f" ({', '.join(pairs)})" if pairs else ""


def _notes_text(facts: Dict[str, Any]) -> str:
    notes = facts.get("notes")
    if isinstance(notes, str) and notes.strip():
        return f" / 요청: {notes.strip()}"
    return ""


def render_from_result(
    *,
    reply: Dict[str, Any],
    plan: Dict[str, Any],
    result: Dict[str, Any],
    trace_id: Optional[str] = None,
) -> str:
    ok = bool(result.get("ok"))
    facts = result.get("facts")
    facts = facts if isinstance(facts, dict) else {}

    # 1) Education
    llm_task = reply.get("llm_task") if isinstance(reply.get("llm_task"), dict) else None
    if isinstance(llm_task, dict):
        kind = llm_task.get("kind")
        if kind == "edu_answer":
            q = str(llm_task.get("question") or "").strip()
            out = generate_education_answer(question=q, trace_id=trace_id)
            if out: return out
        if kind == "edu_summary":
            c = str(llm_task.get("content") or "").strip()
            out = generate_education_summary(content=c, trace_id=trace_id)
            if out: return out
        return _format_template("result.fail.generic", {})

    # 2) Template
    key_ok = reply.get("message_key_ok") or reply.get("message_key") or "fallback.mvp"
    key_fail = reply.get("message_key_fail") or "result.fail.generic"
    message_key = str(key_ok if ok else key_fail)

    vars: Dict[str, Any] = {}
    vars.update(facts)

    if message_key == "result.kiosk.add_item":
        vars["options"] = _options_text(vars)
        vars["notes"] = _notes_text(vars)

    # [수정] General Chat 또는 LLM Reasoning 결과(reject/confirm)일 경우 템플릿 무시
    # 이렇게 해야 "이미 켜져 있어요" 같은 Validator의 동적 메시지가 덮어씌워지지 않습니다.
    status = facts.get("status")
    if status in ["general_chat", "rejected", "conflict_confirm"]:
        text = reply.get("text", "")
    else:
        text = _format_template(message_key, vars)
        if not text.strip() and reply.get("text"):
            text = reply["text"]

    # 3) Surface Rewrite (Driving Persona 적용)
    if ok and _surface_enabled(message_key):
        domain_scope = "kiosk"
        if "driving" in message_key:
            domain_scope = "driving"

        rewritten = surface_rewrite(
            base_text=text, 
            facts=vars, 
            trace_id=trace_id,
            domain=domain_scope 
        )
        if rewritten:
            return rewritten

    return text