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
from rag.pdf_engine import global_pdf_engine

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


# 범용 레벨 가이드 (과목 불문)
LEVEL_PROMPTS = {
    "beginner": (
        "Target Audience: Elementary/Middle school students.\n"
        "Tone: Encouraging, simple, and fun using analogies.\n"
        "Guidelines: Avoid complex jargon. Explain like I'm 10 years old. Use emojis to keep it engaging."
    ),
    "intermediate": (
        "Target Audience: High school/Undergraduate students.\n"
        "Tone: Academic but accessible, clear, and structured.\n"
        "Guidelines: Use standard terminology but define difficult concepts. Focus on key principles and logic."
    ),
    "advanced": (
        "Target Audience: Experts, Graduate students, or Professionals.\n"
        "Tone: Professional, profound, and highly technical.\n"
        "Guidelines: Provide deep insights, theoretical background, and mathematical proofs if necessary. Assume the user has strong background knowledge."
    ),
}

# 기기별 출력 가이드
DEVICE_PROMPTS = {
    "mobile": (
        "Device Context: The user is on a MOBILE device.\n"
        "Formatting: Keep paragraphs short (1-2 sentences). Use bullet points freely. "
        "Avoid wide tables or long code blocks. Use emojis to save space and add context."
    ),
    "web": (
        "Device Context: The user is on a DESKTOP WEB browser.\n"
        "Formatting: Use rich Markdown (bold, italic, tables, code blocks). "
        "Detailed explanations and structured layouts are encouraged."
    ),
    "kiosk": (
        "Device Context: The user is on a PUBLIC KIOSK.\n"
        "Formatting: Extremely concise and direct. Large text friendly. "
        "No scroll if possible. Max 3-4 sentences."
    ),
    "speaker": (
        "Device Context: The user is using a SMART SPEAKER (Voice only).\n"
        "Formatting: Do NOT use Markdown, tables, or lists. Write in spoken conversational style. "
        "Keep it very brief and audible-friendly."
    )
}

# [NEW] 연령대별 페르소나/톤 가이드 추가
AGE_PROMPTS = {
    "child": (
        "Persona: Friendly Kindergarten Teacher or Loving Parent.\n"
        "Tone: Extremely gentle, warm, and affectionate. Use soft sentence endings (~해요, ~지요) and many emojis (🌟, 🐥, ✨).\n"
        "Instruction: Never say 'You are wrong'. Instead say 'That's a great try! How about thinking this way?'. "
        "Make the user feel special and smart. Use very simple words."
    ),
    "teen": (
        "Persona: Cool Mentor or Older Sibling.\n"
        "Tone: Casual, relatable, and not too stiff. Can use slight slang or internet terminology if appropriate.\n"
        "Instruction: Focus on 'Why' it matters. Don't lecture; guide them to the answer. Keep it engaging."
    ),
    "adult": (
        "Persona: Professional Consultant or Professor.\n"
        "Tone: Polite, respectful, and efficient.\n"
        "Instruction: Get straight to the point. Provide value and depth."
    )
}


def generate_edu_answer_with_llm(
    *,
    task_input: Dict[str, Any],
    user_message: str,
    trace_id: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    
    intent = ((task_input.get("intent") or "") if isinstance(task_input, dict) else "").strip()
    slots = (task_input.get("slots") or {}) if isinstance(task_input.get("slots"), dict) else {}
    meta = (task_input.get("meta") or {}) if isinstance(task_input.get("meta"), dict) else {}

    # ----------------------------------------------------
    # 0. PDF RAG Retrieval Check
    # ----------------------------------------------------
    pdf_context = ""
    # [TWEAK] 의도가 명확한 경우나 RAG가 필요한 경우에만 검색하도록 조건 개선 가능
    # 현재는 Global flag만 확인
    if global_pdf_engine.has_data:
        # 질문과 관련된 내용을 PDF에서 검색
        retrieved_text = global_pdf_engine.search(user_message, top_k=3)
        if retrieved_text:
            pdf_context = (
                f"\n[REFERENCE MATERIAL FROM PDF ({global_pdf_engine.filename})]\n"
                f"{retrieved_text}\n"
                "---------------------------------------------------\n"
                "INSTRUCTION: Prioritize the information above to answer the user's question.\n"
            )
            if log_event and trace_id:
                log_event(trace_id, "pdf_rag_hit", {"filename": global_pdf_engine.filename})

    # ----------------------------------------------------
    # 1. UI Navigation Detection & Search
    # ----------------------------------------------------
    is_nav = (intent == "ask_ui_navigation") or _is_ui_navigation_question(user_message)

    if is_nav:
        try:
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
    # 2. General LLM Generation (Universal Tutor)
    # ----------------------------------------------------
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is empty")

    model = os.getenv("OPENAI_EDU_MODEL", os.getenv("OPENAI_NLU_MODEL", "gpt-4o-mini")).strip()
    state = (task_input.get("state") or {}) if isinstance(task_input.get("state"), dict) else {}

    # --- [A] Level & Profile Extraction ---
    # 1. Level
    lvl_slot = slots.get("level")
    if isinstance(lvl_slot, dict):
        user_lvl = lvl_slot.get("value")
    else:
        user_lvl = lvl_slot
    if not user_lvl:
        user_lvl = meta.get("user_level")
    
    level_key = str(user_lvl).lower() if user_lvl else "advanced"
    level_instruction = LEVEL_PROMPTS.get(level_key, LEVEL_PROMPTS["advanced"])

    # 2. Device Type
    device_type = meta.get("device_type", "web").lower() # default to web
    device_instruction = DEVICE_PROMPTS.get(device_type, DEVICE_PROMPTS["web"])

    # 3. Subject/Domain Context
    subj_slot = slots.get("subject")
    user_subject = None
    if isinstance(subj_slot, dict):
        user_subject = subj_slot.get("value")
    if not user_subject:
        user_subject = meta.get("domain_context") or meta.get("subject") # meta fallback
    
    subject_instruction = ""
    if user_subject:
        subject_instruction = (
            f"\n[SUBJECT CONTEXT]\n"
            f"Current Subject: {user_subject}\n"
            f"Instruction: Interpret all questions within the context of '{user_subject}'. "
            f"For ambiguous terms (e.g., 'Big Bang', 'Root', 'Solution'), use the definition relevant to {user_subject}.\n"
        )
    
    # 4. Learner Profile (Lang, Exam, WeakPoints, Age)
    native_lang = meta.get("native_language")
    target_exam = meta.get("target_exam")
    weak_points = meta.get("weak_points")
    # [NEW] Age Group Prompt Selection
    age_group = meta.get("user_age_group") or ""
    age_instruction = AGE_PROMPTS.get(age_group.lower(), "") 

    profile_instruction = "\n[LEARNER PROFILE]\n"
    has_profile = False
    
    if age_instruction:
        profile_instruction += f"{age_instruction}\n"
        has_profile = True
        
    if native_lang:
        profile_instruction += f"- Native Language: {native_lang} (Explain concepts using comparisons to this language if helpful).\n"
        has_profile = True
    if target_exam:
        profile_instruction += f"- Target Exam: {target_exam} (Align difficulty and terminology with this exam standard).\n"
        has_profile = True
    if weak_points and isinstance(weak_points, list):
        profile_instruction += f"- Weak Points: {', '.join(weak_points)} (Provide extra detail/repetition on these topics).\n"
        has_profile = True
    
    if not has_profile:
        profile_instruction = ""

    # 만능 튜터 시스템 프롬프트 (PDF Context 주입 포함)
    base_system = (
        "You are a 'Universal AI Tutor' capable of teaching any subject (Math, Science, History, Languages, etc.).\n"
        "Your goal is to help the user learn and understand concepts clearly.\n"
        "\n"
        "CORE INSTRUCTIONS:\n"
        "1. **Subject Agnostic**: You can answer questions about Physics, Coding, Spanish, Korean History, etc.\n"
        "2. **Factuality**: Do NOT invent facts. If you don't know, admit it.\n"
        "3. **Format**: Use Markdown (bolding, lists) to make explanations easy to read.\n"
        "4. **Navigation**: If the user explicitly asks for UI menu navigation, handle it. Otherwise, focus on teaching.\n"
        "5. **UI Hints**: In ui_hints, ALWAYS include keys: domain, intent, menu_name, breadcrumb, url.\n"
        "6. **Output**: Return JSON ONLY matching the schema.\n"
        f"{pdf_context}"
    )

    # 프롬프트 조합
    system = (
        f"{base_system}\n"
        f"\n[TARGET AUDIENCE ADAPTATION]\n{level_instruction}\n"
        f"\n[DEVICE OPTIMIZATION]\n{device_instruction}\n"
        f"{subject_instruction}"
        f"{profile_instruction}"
    )
    
    # 히스토리 전체 주입
    history_text = ""
    if history:
        lines = []
        for h in history:
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
        "level_setting": level_key,
        "device_setting": device_type,
        "subject_setting": user_subject,
        "age_setting": age_group,
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
            "device": device_type,
            "subject": user_subject,
            "age_group": age_group,
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

    # ✅ [NEW] RAG 사용 여부 플래그 추가
    ui_hints["used_pdf_rag"] = bool(pdf_context)

    return {"text": text, "ui_hints": ui_hints}