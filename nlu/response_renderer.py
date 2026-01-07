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


def _surface_enabled(message_key: str, domain_hint: str = "") -> bool:
    # [New] Companion 모드는 항상 Surface Rewrite 사용
    if domain_hint == "companion":
        return True
    
    # [Existing] 기존 로직 유지
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
    meta: Optional[Any] = None,  # [New] Optional added
    state: Optional[Dict[str, Any]] = None, # [New] Optional added
) -> str:
    """
    최종 응답 텍스트를 생성합니다.
    우선순위:
    1. LLM Task 실행 결과 (result['text'] 또는 result['llm_output'])
    2. Validator가 지정한 고정 텍스트 (reply['text'] - 단, Fallback/Reject 상황일 때만)
    3. 템플릿 메시지 (message_key)
    """
    ok = bool(result.get("ok"))
    facts = result.get("facts")
    facts = facts if isinstance(facts, dict) else {}
    
    domain = plan.get("domain", "kiosk")

    # ---------------------------------------------------------
    # 1. [최우선] LLM 생성 결과가 있는지 확인 (Education 등)
    # ---------------------------------------------------------
    # (A) 오케스트레이터가 결과를 result['text']에 병합했을 가능성
    if result.get("text"):
        return str(result["text"])

    # (B) result['llm_output'] 키에 담겨있을 가능성
    # facts나 reply 내부까지 확인하여 누락 방지
    llm_out = result.get("llm_output") or facts.get("llm_output") or reply.get("llm_output")
    
    if llm_out:
        if isinstance(llm_out, dict):
            # text 필드 우선, 없으면 content 확인
            val = llm_out.get("text") or llm_out.get("content") or llm_out.get("reply")
            if val: return str(val)
        if isinstance(llm_out, str):
            return llm_out

    # ---------------------------------------------------------
    # 2. Legacy LLM Task (결과가 아직 없는 경우 - 함수 호출)
    # ---------------------------------------------------------
    llm_task = reply.get("llm_task")
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
        
        # 처리되지 않은 태스크가 있다면 Fallback 템플릿 대신 안내 메시지 반환
        return "잠시만 기다려주세요. 답변을 생성하고 있습니다."

    # ---------------------------------------------------------
    # 3. Template / Validator Text Rendering
    # ---------------------------------------------------------
    key_ok = reply.get("message_key_ok") or reply.get("message_key") or "fallback.mvp"
    key_fail = reply.get("message_key_fail") or "result.fail.generic"
    message_key = str(key_ok if ok else key_fail)

    vars: Dict[str, Any] = {}
    vars.update(facts)

    if message_key == "result.kiosk.add_item":
        vars["options"] = _options_text(vars)
        vars["notes"] = _notes_text(vars)

    status = facts.get("status")
    
    # (A) Validator가 직접 텍스트를 지정한 경우 (General Chat, Reject 등)
    # 템플릿보다 reply['text']를 우선시해야 하는 상황들
    if status in ["general_chat", "rejected", "conflict_confirm"] or not TEMPLATES.get(message_key):
        if reply.get("text"):
            text = reply["text"]
        else:
            # 템플릿도 없고 텍스트도 없으면 Fallback
            text = _format_template(message_key, vars)
    else:
        # (B) 템플릿 사용 (Kiosk, Driving Success 등)
        text = _format_template(message_key, vars)

    # 4. Surface Rewrite (Persona 적용)
    # Education 도메인은 보통 LLM이 톤을 조절하므로 Surface Rewrite를 스킵하는 것이 일반적입니다.
    # [Updated] _surface_enabled에 domain 힌트 전달
    if ok and _surface_enabled(message_key, domain_hint=domain):
        domain_scope = "kiosk"
        if domain == "companion":
            domain_scope = "companion"
        elif "driving" in message_key or domain == "driving":
            domain_scope = "driving"

        rewritten = surface_rewrite(
            base_text=text, 
            facts=vars, 
            trace_id=trace_id,
            domain=domain_scope,
            meta=meta,   # [New] Pass meta
            state=state  # [New] Pass state
        )
        if rewritten:
            return rewritten

    return text