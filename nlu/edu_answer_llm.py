from __future__ import annotations

import os
import json
import re
from typing import Any, Dict, Optional, List

import requests

try:
    from utils.logging import log_event
except Exception:
    log_event = None

from rag.site_nav_retriever import search_site_nav

OPENAI_API_URL = "https://api.openai.com/v1/responses"

# ----------------------------
# UI navigation intent detect helpers
# ----------------------------
_NAV_KW = [
    "메뉴", "페이지", "어디", "어디에", "경로", "들어가", "찾아", "위치", "바로가기", "링크",
]
_NAV_RE = re.compile(r"(.+?)(메뉴|페이지).*(어디|어디에|경로|위치)|어디(에)?\s*있", re.IGNORECASE)


def _is_ui_navigation_question(user_message: str) -> bool:
    s = (user_message or "").strip()
    if not s:
        return False
    hit = 0
    for k in _NAV_KW:
        if k in s:
            hit += 1
    if hit >= 2:
        return True
    if _NAV_RE.search(s):
        return True
    return False


def _extract_menu_candidate(user_message: str) -> str:
    s = (user_message or "").strip()
    s = re.sub(r"(메뉴|페이지)\s*(가|는|를|이)?\s*(어디|어디에|어딨어|어딨|어디있|위치|경로).*$", "", s)
    s = re.sub(r"(어디|어디에|어딨어|어딨|어디있).*$", "", s)
    s = re.sub(r"(알려(줘|주세요)|찾아(줘|주세요)|부탁(해|해요)|궁금(해|해요)).*$", "", s)
    s = " ".join(s.split()).strip()
    return s if len(s) >= 2 else (user_message or "").strip()


# ----------------------------
# 1. Retrieval Router
# ----------------------------
class RetrievalRouter:
    """
    사용자 의도와 메시지에 따라 적절한 답변 소스(RAG/LLM)를 결정합니다.
    """
    @staticmethod
    def route(intent: str, user_message: str, slots: Dict[str, Any]) -> str:
        # 1) 명시적 UI 네비게이션 인텐트
        if intent == "ask_ui_navigation":
            return "site_nav"
        
        # 2) 키워드 기반 UI 네비게이션 감지 (Fallback)
        if _is_ui_navigation_question(user_message):
            return "site_nav"
        
        # 3) 지식 검색 (Knowledge Base) - 추후 VectorDB 연결 예정
        # 현재는 ask_knowledge 인텐트가 오면 LLM이 처리하되, 필요시 문맥을 주입하는 구조 준비
        if intent == "ask_knowledge":
            return "knowledge_base" # 현재 구현상 LLM으로 fallback됨

        # 4) 기본 LLM 생성
        return "llm_generate"


# ----------------------------
# 2. Handlers
# ----------------------------

def _handle_site_nav(user_message: str, slots: Dict[str, Any], trace_id: Optional[str]) -> Dict[str, Any]:
    # 슬롯에서 메뉴명 추출 시도, 없으면 메시지에서 추출
    q = slots.get("menu_name_query", {}).get("value")
    if not q:
        q = _extract_menu_candidate(user_message)
    
    hits = search_site_nav(query=q, topk=3)
    
    if log_event and trace_id:
        log_event(
            trace_id,
            "edu_site_nav_rag_search",
            {
                "query": q,
                "hit_count": len(hits),
                "top": getattr(hits[0], "menu_name", None) if hits else None,
            },
        )

    if not hits:
        return {
            "text": f"'{q}' 메뉴를 사이트에서 찾지 못했어요. 메뉴 이름을 조금 더 정확히 알려주시면 다시 찾아드릴게요.",
            "ui_hints": {
                "domain": "education",
                "intent": "ask_ui_navigation",
                "menu_name": "",
                "breadcrumb": "",
                "url": "",
            },
        }

    top = hits[0]
    lines = []
    lines.append(f"'{top.menu_name}'는 **{top.breadcrumb}** 경로에서 찾을 수 있어요.")
    lines.append(f"바로가기: {top.url}")

    if len(hits) >= 2:
        lines.append("")
        lines.append("비슷한 메뉴 후보:")
        for h in hits[1:]:
            lines.append(f"- {h.menu_name} → {h.breadcrumb} / {h.url}")

    return {
        "text": "\n".join(lines).strip(),
        "ui_hints": {
            "domain": "education",
            "intent": "ask_ui_navigation",
            "menu_name": getattr(top, "menu_name", "") or "",
            "breadcrumb": getattr(top, "breadcrumb", "") or "",
            "url": getattr(top, "url", "") or "",
        },
    }


def _openai_call_json_schema(
    *,
    model: str,
    system: str,
    user_obj: Dict[str, Any],
    schema_name: str,
    json_schema: Dict[str, Any],
    api_key: str,
    timeout: int = 25,
) -> Dict[str, Any]:
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": json_schema,
            }
        },
        "store": False,
    }

    r = requests.post(
        OPENAI_API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"OpenAI error {r.status_code}: {r.text[:1200]}")

    resp_json = r.json()

    if isinstance(resp_json.get("output_text"), str) and resp_json["output_text"].strip():
        return json.loads(resp_json["output_text"].strip())

    output = resp_json.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if isinstance(c.get("text"), str) and c["text"].strip():
                    return json.loads(c["text"].strip())

    raise ValueError("Could not parse Responses output JSON")


def _edu_generation_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string"},
            "ui_hints": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "domain": {"type": "string"},
                    "intent": {"type": "string"},
                    "menu_name": {"type": "string"},
                    "breadcrumb": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["domain", "intent", "menu_name", "breadcrumb", "url"],
            },
        },
        "required": ["text", "ui_hints"],
    }


def _handle_llm_generate(
    task_input: Dict[str, Any],
    user_message: str,
    trace_id: Optional[str]
) -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is empty")

    model = os.getenv("OPENAI_EDU_MODEL", os.getenv("OPENAI_NLU_MODEL", "gpt-4o-mini")).strip()

    intent = ((task_input.get("intent") or "") if isinstance(task_input, dict) else "").strip()
    slots = (task_input.get("slots") or {}) if isinstance(task_input.get("slots"), dict) else {}
    meta = (task_input.get("meta") or {}) if isinstance(task_input.get("meta"), dict) else {}
    state = (task_input.get("state") or {}) if isinstance(task_input.get("state"), dict) else {}

    system = (
        "You are an educational assistant for Korean language learning.\n"
        "IMPORTANT:\n"
        "- Do NOT invent facts.\n"
        "- If the task is rewrite/expand/summarize, do NOT add examples or new information not present in the provided content.\n"
        "- Follow the user's constraints strictly (e.g., number of sentences).\n"
        "- Output JSON ONLY matching the schema.\n"
        "- In ui_hints, ALWAYS include keys: domain, intent, menu_name, breadcrumb, url.\n"
        "- If menu navigation info is not applicable, set menu_name/breadcrumb/url to empty strings.\n"
    )

    user_obj = {
        "user_message": user_message,
        "intent": intent,
        "slots": slots,
        "meta": meta,
        "state_summary": {
            "conversation_id": state.get("conversation_id"),
            "turn_index": state.get("turn_index"),
            "history_summary": state.get("history_summary", ""),
        },
    }

    out = _openai_call_json_schema(
        model=model,
        system=system,
        user_obj=user_obj,
        schema_name="edu_answer_generation",
        json_schema=_edu_generation_schema(),
        api_key=api_key,
        timeout=25,
    )

    if log_event and trace_id:
        log_event(trace_id, "edu_llm_generate_ok", {"model": model, "intent": intent, "out_keys": list(out.keys())})

    text = (out.get("text") or "").strip()
    ui_hints = out.get("ui_hints") if isinstance(out.get("ui_hints"), dict) else {}
    ui_hints.setdefault("domain", "education")
    ui_hints.setdefault("intent", intent or "ask_question")

    # ✅ strict schema 보정
    ui_hints.setdefault("menu_name", "")
    ui_hints.setdefault("breadcrumb", "")
    ui_hints.setdefault("url", "")

    for k in ("domain", "intent", "menu_name", "breadcrumb", "url"):
        if not isinstance(ui_hints.get(k), str):
            ui_hints[k] = str(ui_hints.get(k) or "")

    return {"text": text, "ui_hints": ui_hints}


# ----------------------------
# 3. Main Entry Point
# ----------------------------
def generate_edu_answer_with_llm(
    *,
    task_input: Dict[str, Any],
    user_message: str,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    
    intent = ((task_input.get("intent") or "") if isinstance(task_input, dict) else "").strip()
    slots = (task_input.get("slots") or {}) if isinstance(task_input.get("slots"), dict) else {}

    # 1. Routing
    route = RetrievalRouter.route(intent, user_message, slots)

    if log_event and trace_id:
        log_event(trace_id, "edu_router_decision", {"route": route, "intent": intent})

    # 2. Execution based on route
    if route == "site_nav":
        try:
            return _handle_site_nav(user_message, slots, trace_id)
        except Exception as e:
            if log_event and trace_id:
                log_event(trace_id, "edu_site_nav_fail", {"err": str(e)})
            # 실패 시 LLM으로 fallback
            return _handle_llm_generate(task_input, user_message, trace_id)

    elif route == "knowledge_base":
        # TODO: VectorDB 연결 시 여기에 로직 추가. 현재는 LLM 생성으로 처리.
        pass

    # Default: LLM Generation
    return _handle_llm_generate(task_input, user_message, trace_id)