# nlu/executor.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from utils.logging import log_event

from domain.kiosk import policy as kiosk_policy

from nlu.llm_answer_client import answer_with_openai


def _safe_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _safe_str(x: Any) -> str:
    return x if isinstance(x, str) else "" if x is None else str(x)


def _strip_nulls(d: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (d or {}).items():
        if v is None:
            continue
        out[k] = v
    return out


def _build_kiosk_reco_prompt(user_message: str, rag_context: Dict[str, Any]) -> Dict[str, str]:
    """
    추천은 '메뉴DB 기반'으로만 답하게 강제하는 프롬프트.
    - 환각 메뉴 방지
    - menu가 비면 조건을 되묻기
    """
    menu = rag_context.get("menu") if isinstance(rag_context, dict) else None
    menu = menu if isinstance(menu, list) else []

    sys = (
        "You are a kiosk ordering assistant.\n"
        "You MUST recommend ONLY from the provided menu list in the context.\n"
        "If the menu list is empty, apologize and ask the user for constraints (category/budget/dietary).\n"
        "Keep the answer short and practical.\n"
        "Return plain text (no JSON)."
    )

    # 메뉴를 너무 길게 붙이지 말고(토큰 절약), 구조화 요약으로 제공
    menu_lines = []
    for it in menu[:10]:
        name = _safe_str(it.get("name"))
        price = it.get("price")
        cat = _safe_str(it.get("category"))
        if price is None:
            menu_lines.append(f"- {name} ({cat})")
        else:
            menu_lines.append(f"- {name} ({cat}, {price} KRW)")

    ctx = "MENU CANDIDATES:\n" + ("\n".join(menu_lines) if menu_lines else "(empty)")

    user = f"{ctx}\n\nUSER: {user_message}"

    return {"system": sys, "user": user}


def maybe_execute_llm_task(
    *,
    reply: Dict[str, Any],
    plan: Dict[str, Any],
    meta: Dict[str, Any],
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    validator가 llm_task를 붙였을 때만 실행.
    """
    r = dict(reply or {})
    llm_task = _safe_dict(r.get("llm_task"))
    if not llm_task:
        return r

    kind = _safe_str(llm_task.get("kind"))
    slots = _safe_dict(llm_task.get("slots"))
    domain = _safe_str((plan or {}).get("domain"))
    intent = _safe_str((plan or {}).get("intent"))

    # ✅ kiosk 추천(RAG)
    if kind == "kiosk_ask_recommendation":
        user_message = _safe_str(meta.get("user_message"))  # meta에 없을 수 있음
        if not user_message:
            # plan/slots에서라도 가져오기
            user_message = _safe_str(slots.get("query") or "") or "추천 메뉴 알려줘"

        # 메뉴DB 조회 -> RAG context
        rag = kiosk_policy.build_menu_rag_context_for_recommendation(
            req={"meta": meta},  # policy는 req에서 meta를 보므로 dict로 감싸서 전달
            slots=slots,
            limit=10,
        )

        prompt = _build_kiosk_reco_prompt(user_message, rag)

        model = os.getenv("OPENAI_ANSWER_MODEL") or os.getenv("OPENAI_NLU_MODEL") or "gpt-4o-mini"

        # answer_with_openai는 프로젝트 기존 함수를 가정(너 코드에 맞춰 인자명만 조정하면 됨)
        text = answer_with_openai(
            model=model,
            system_prompt=prompt["system"],
            user_prompt=prompt["user"],
        )

        r["text"] = _safe_str(text).strip()
        log_event(trace_id, "kiosk_llm_ok", {"kind": kind, "model": model, "text_len": len(r["text"])})
        return r

    # ✅ education llm_task 등은 기존 로직 유지(너 기존 answer_client 흐름대로 연결)
    # (여긴 네 기존 executor 구조에 맞춰서 그대로 두면 됨)
    return r
