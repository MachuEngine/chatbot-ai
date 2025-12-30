# nlu/edu_answer_llm.py
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
    "메뉴", "페이지", "어디", "어디에", "경로", "들어가", "찾아", "위치", "바로가기", "링크", "사이트", "주소"
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


def _clean_query(q: str) -> str:
    """검색 정확도를 떨어뜨리는 불용어 제거"""
    stops = ["메뉴", "페이지", "링크", "사이트", "주소", "어디", "알려줘", "찾아줘", "보여줘", "가르쳐줘"]
    for s in stops:
        q = q.replace(s, "")
    return q.strip()


def _extract_menu_candidate(user_message: str) -> str:
    # 정규식 기반 추출 (Fallback)
    s = (user_message or "").strip()
    
    # "~~메뉴 어디" 패턴에서 앞부분 추출 시도
    m = re.match(r"(.+?)\s*(메뉴|페이지|링크|사이트)", s)
    if m:
        return _clean_query(m.group(1))

    # 일반적인 제거 로직
    s = re.sub(r"(메뉴|페이지|링크|사이트)\s*(가|는|를|이)?\s*(어디|어디에|어딨어|어딨|어디있|위치|경로).*$", "", s)
    s = re.sub(r"(어디|어디에|어딨어|어딨|어디있).*$", "", s)
    s = re.sub(r"(알려(줘|주세요)|찾아(줘|주세요)|부탁(해|해요)|궁금(해|해요)).*$", "", s)
    s = " ".join(s.split()).strip()
    
    return _clean_query(s) if len(s) >= 2 else (user_message or "").strip()


def _render_nav_answer(query: str, hits: List[Any]) -> Dict[str, Any]:
    if not hits:
        return {
            "text": f"'{query}' 관련 메뉴를 찾지 못했어요. 메뉴명을 조금 더 정확히 말씀해 주시겠어요?",
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
    lines.append(f"**{top.menu_name}** 메뉴는 **{top.breadcrumb}** 경로에 있습니다.")
    lines.append(f"바로가기: {top.url}")

    if len(hits) >= 2:
        lines.append("\n비슷한 메뉴:")
        for h in hits[1:]:
            lines.append(f"- {h.menu_name} ({h.breadcrumb})")

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


# ✅ [추가] 3단계 레벨별 프롬프트 가이드
LEVEL_PROMPTS = {
    # beginner -> 초등학생 수준
    "beginner": (
        "Target Audience: Elementary school students (Age 8-13).\n"
        "Tone: Friendly, simple, and encouraging.\n"
        "Guidelines: Use very easy vocabulary and short sentences. Use fun analogies to explain concepts."
    ),

    # intermediate -> 중~고등학생 수준
    "intermediate": (
        "Target Audience: Middle & High school students (Age 14-19).\n"
        "Tone: Helpful, clear, and academic-lite.\n"
        "Guidelines: Explain concepts clearly suitable for exam preparation. Use standard terminology but verify understanding."
    ),

    # advanced -> 대학교 이상 수준 (기본값)
    "advanced": (
        "Target Audience: University students and Adults.\n"
        "Tone: Professional, academic, and detailed.\n"
        "Guidelines: Treat the user as an educated adult. Provide deep insights, theoretical background, and comprehensive answers."
    ),
}


def generate_edu_answer_with_llm(
    *,
    task_input: Dict[str, Any],
    user_message: str,
    trace_id: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None, # ✅ [변경] history 인자 추가
) -> Dict[str, Any]:
    
    intent = ((task_input.get("intent") or "") if isinstance(task_input, dict) else "").strip()
    slots = (task_input.get("slots") or {}) if isinstance(task_input.get("slots"), dict) else {}
    
    # ----------------------------------------------------
    # 1. UI Navigation Detection & Search
    # ----------------------------------------------------
    is_nav = (intent == "ask_ui_navigation") or _is_ui_navigation_question(user_message)

    if is_nav:
        try:
            # 검색어 결정: NLU Slot 우선 사용 -> 없으면 정규식 추출 -> 불용어 정리
            slot_q = slots.get("menu_name_query", {}).get("value")
            raw_q = slot_q if slot_q else _extract_menu_candidate(user_message)
            final_q = _clean_query(raw_q)

            if final_q and len(final_q) >= 1:
                hits = search_site_nav(query=final_q, topk=3)
                
                if log_event and trace_id:
                    log_event(trace_id, "edu_site_nav_rag_attempt", {
                        "slot_q": slot_q, 
                        "final_q": final_q, 
                        "hits": len(hits)
                    })

                if hits:
                    return _render_nav_answer(final_q, hits)
        except Exception as e:
            if log_event and trace_id:
                log_event(trace_id, "edu_site_nav_rag_fail", {"err": str(e)[:400]})

    # ----------------------------------------------------
    # 2. General LLM Generation (Fallback)
    # ----------------------------------------------------
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is empty")

    model = os.getenv("OPENAI_EDU_MODEL", os.getenv("OPENAI_NLU_MODEL", "gpt-4o-mini")).strip()
    meta = (task_input.get("meta") or {}) if isinstance(task_input.get("meta"), dict) else {}
    state = (task_input.get("state") or {}) if isinstance(task_input.get("state"), dict) else {}

    # ✅ [변경] 레벨 감지 및 프롬프트 선택
    # 1) Slot에서 확인
    lvl_slot = slots.get("level")
    if isinstance(lvl_slot, dict):
        user_lvl = lvl_slot.get("value")
    else:
        user_lvl = lvl_slot

    # 2) Slot 없으면 Meta에서 확인
    if not user_lvl:
        user_lvl = meta.get("user_level")

    # 3) 기본값 fallback (advanced)
    level_key = str(user_lvl).lower() if user_lvl else "advanced"
    level_instruction = LEVEL_PROMPTS.get(level_key, LEVEL_PROMPTS["advanced"])

    base_system = (
        "You are an educational assistant for Korean language learning.\n"
        "IMPORTANT:\n"
        "- Do NOT invent facts.\n"
        "- If the user asks for menu navigation and you couldn't find it, apologize and ask for the exact menu name.\n"
        "- Output JSON ONLY matching the schema.\n"
        "- In ui_hints, ALWAYS include keys: domain, intent, menu_name, breadcrumb, url.\n"
    )

    # ✅ [변경] 시스템 프롬프트에 레벨 지침 + 히스토리 주입
    system = f"{base_system}\n[TARGET AUDIENCE ADAPTATION]\n{level_instruction}\n"
    
    # 히스토리 텍스트 변환
    history_text = ""
    if history:
        # 최근 5개만 텍스트로 변환 (토큰 절약)
        recent_history = history[-30:]
        lines = []
        for h in recent_history:
            role = h.get("role", "unknown")
            content = h.get("content", "")
            if content:
                lines.append(f"{role}: {content}")
        if lines:
            history_text = "Conversation History:\n" + "\n".join(lines) + "\n"

    system += f"\n{history_text}"

    user_obj = {
        "user_message": user_message,
        "intent": intent,
        "slots": slots,
        "meta": meta,
        "level_setting": level_key,  # 디버깅 정보
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
        log_event(trace_id, "edu_llm_generate_ok", {
            "model": model, 
            "intent": intent, 
            "level": level_key, 
            "history_len": len(history) if history else 0,
            "out_keys": list(out.keys())
        })

    text = (out.get("text") or "").strip()
    ui_hints = out.get("ui_hints") if isinstance(out.get("ui_hints"), dict) else {}
    ui_hints.setdefault("domain", "education")
    ui_hints.setdefault("intent", intent or "ask_question")

    # Strict schema 보정
    ui_hints.setdefault("menu_name", "")
    ui_hints.setdefault("breadcrumb", "")
    ui_hints.setdefault("url", "")

    for k in ("domain", "intent", "menu_name", "breadcrumb", "url"):
        if not isinstance(ui_hints.get(k), str):
            ui_hints[k] = str(ui_hints.get(k) or "")

    return {"text": text, "ui_hints": ui_hints}