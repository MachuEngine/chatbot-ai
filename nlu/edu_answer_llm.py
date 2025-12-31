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


# ------------------------------------------------------------------
# [Prompt Templates] 모든 메타데이터 반영을 위한 템플릿 정의
# ------------------------------------------------------------------

# 1. 학습 레벨 가이드
LEVEL_PROMPTS = {
    "beginner": (
        "LEVEL: Elementary/Beginner.\n"
        "INSTRUCTION: Use simple analogies and everyday examples. Avoid complex jargon. "
        "Keep sentences short and easy to digest."
    ),
    "intermediate": (
        "LEVEL: High School/Undergraduate.\n"
        "INSTRUCTION: Use standard terminology but briefly define difficult concepts. "
        "Focus on 'Why' and 'How'. Balance theory and practice."
    ),
    "advanced": (
        "LEVEL: Expert/Professional.\n"
        "INSTRUCTION: Provide deep technical insights, theoretical background, and edge cases. "
        "Assume strong domain knowledge. Be concise and precise."
    ),
}

# 2. 기기 환경 가이드
DEVICE_PROMPTS = {
    "mobile": "FORMAT: Mobile friendly. Short paragraphs, bullet points, and emojis. No wide tables.",
    "web": "FORMAT: Desktop view. Rich Markdown (tables, code blocks allowed). Detailed explanations allowed.",
    "kiosk": "FORMAT: Kiosk view. Extremely short and punchy. Max 3 sentences. Very large text style.",
    "speaker": "FORMAT: Voice only. Conversational style. No Markdown, no lists, no visual references."
}

# 3. 연령대별 페르소나 (Age Group)
AGE_PROMPTS = {
    "child": (
        "TARGET: Child (5-10yo). Be like a Friendly Kindergarten Teacher.\n"
        "TONE: Warm, encouraging, and enthusiastic. Use soft sentence endings (~해요, ~지요) and many emojis (🌟, 🐥, ✨).\n"
        "RULE: Never say 'Wrong'. Say 'Good try!'. Make learning feel like play."
    ),
    "teen": (
        "TARGET: Teenager. Be like a Cool Mentor or Older Sibling.\n"
        "TONE: Casual, relatable, and witty. Not too stiff. Can use mild internet slang if appropriate.\n"
        "RULE: Don't lecture. Focus on practical value and 'Why this matters'."
    ),
    "adult": (
        "TARGET: Adult. Be a Professional Consultant.\n"
        "TONE: Polite, respectful, and efficient (해요체 or 하십시오체).\n"
        "RULE: Respect the user's time. Get straight to the point with high-quality information."
    )
}

# 4. 말투/스타일 (Tone Style)
TONE_PROMPTS = {
    "kind": "STYLE: Extremely Kind & Encouraging. Praise often. Use emojis (✨, 👍).",
    "strict": "STYLE: Strict & Professor-like. Point out errors directly. No fluff. Formal tone.",
    "socratic": "STYLE: Socratic Method. Do NOT give the answer directly. Ask guiding questions to help the user think.",
    "humorous": "STYLE: Humorous & Witty. Use jokes, fun metaphors, and lighthearted language."
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
    if global_pdf_engine.has_data:
        retrieved_text = global_pdf_engine.search(user_message, top_k=3)
        if retrieved_text:
            pdf_context = (
                f"\n[REFERENCE MATERIAL (Must Prioritize)]\n"
                f"{retrieved_text}\n"
                "---------------------------------------------------\n"
                "INSTRUCTION: Answer based on the reference material above if relevant.\n"
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

    # --- [A] Context Assembly: ALL Metadata ---
    
    # 1. Level
    lvl_slot = slots.get("level")
    user_lvl = (lvl_slot.get("value") if isinstance(lvl_slot, dict) else lvl_slot) or meta.get("user_level")
    level_key = str(user_lvl).lower() if user_lvl else "intermediate"
    level_inst = LEVEL_PROMPTS.get(level_key, LEVEL_PROMPTS["intermediate"])

    # 2. Device Type
    device_type = str(meta.get("device_type", "web")).lower()
    device_inst = DEVICE_PROMPTS.get(device_type, DEVICE_PROMPTS["web"])

    # 3. Subject Context (과목별 강력 지침 추가)
    subj_slot = slots.get("subject")
    user_subject = (subj_slot.get("value") if isinstance(subj_slot, dict) else None) or meta.get("subject")
    
    subject_inst = ""
    # "general"이 아니거나 값이 있을 때만 지침 생성
    if user_subject and str(user_subject).lower() not in ["general", ""]:
        subj_lower = str(user_subject).lower()
        additional_guidance = ""
        
        # ✅ [핵심] 코딩 과목일 경우 구현/알고리즘 중심 설명 강제
        if subj_lower in ["coding", "programming", "computer science", "it", "code"]:
            additional_guidance = (
                " IMPORTANT: You are explaining this in a Programming/IT context. "
                "Provide code examples (Python/JS) and explain the algorithmic logic (e.g., using arrays, loops, or randomization). "
                "Do NOT explain it as a physical game unless asked."
            )
        elif subj_lower in ["math", "mathematics"]:
            additional_guidance = " Provide formulas, step-by-step proofs, and calculations."
        
        subject_inst = (
            f"CURRENT SUBJECT: {user_subject}\n"
            f"RULE: Interpret all terms and questions strictly within the domain of '{user_subject}'. "
            f"If a word has multiple meanings, choose the definition used in {user_subject}.{additional_guidance}"
        )
    else:
        subject_inst = "CURRENT SUBJECT: General Knowledge. Answer broadly unless specified otherwise."

    # 4. Detailed Learner Profile
    age_group = str(meta.get("user_age_group") or "adult").lower()
    age_inst = AGE_PROMPTS.get(age_group, AGE_PROMPTS["adult"])

    tone_style = str(meta.get("tone_style") or "").lower()
    tone_inst = TONE_PROMPTS.get(tone_style, "")

    native_lang = meta.get("native_language")
    target_exam = meta.get("target_exam")
    weak_points = meta.get("weak_points")

    profile_lines = []
    if native_lang and str(native_lang).lower() not in ["ko", "korean", ""]:
        profile_lines.append(f"- Native Language: {native_lang} (Use analogies from this culture/language if helpful).")
    
    if target_exam:
        profile_lines.append(f"- Goal Exam: {target_exam} (Align difficulty and terms with this exam standard).")
    
    if weak_points and isinstance(weak_points, list):
        wp_str = ", ".join(weak_points)
        profile_lines.append(f"- Weak Points: {wp_str} (Provide extra detail and repetition on these topics).")

    profile_section = "\n[LEARNER PROFILE]\n" + "\n".join(profile_lines) if profile_lines else ""

    # ----------------------------------------------------
    # System Prompt Assembly
    # ----------------------------------------------------
    base_system = (
        "You are a 'Universal AI Tutor'. Your goal is to teach efficiently and effectively.\n"
        "STRICTLY follow the persona and context rules below.\n"
        "\n"
        f"{pdf_context}"
        f"\n[1. PERSONA & TONE]\n"
        f"- {age_inst}\n"
        f"- {tone_inst}\n"
        f"\n[2. SUBJECT CONTEXT (Highest Priority)]\n"
        f"{subject_inst}\n"
        f"\n[3. TEACHING LEVEL]\n{level_inst}\n"
        f"\n[4. STUDENT PROFILE]\n{profile_section}\n"
        f"\n[5. FORMAT]\n"
        f"{device_inst}\n"
        "\n"
        "GENERAL RULES:\n"
        "- Use Markdown formatting (bold, lists, code blocks) for readability.\n"
        "- If the user asks for UI/Menu navigation, look at 'ui_hints'.\n"
        "- If you don't know the answer, admit it clearly.\n"
        "- Return JSON matching the schema."
    )

    # 히스토리 주입
    history_text = ""
    if history:
        lines = []
        for h in history:
            role = h.get("role", "unknown")
            content = h.get("content", "")
            if content:
                lines.append(f"{role}: {content}")
        if lines:
            history_text = "\n[CONVERSATION HISTORY]\n" + "\n".join(lines) + "\n"

    system = base_system + history_text

    # ✅ [수정] LLM에게 질문을 보낼 때, Subject Context를 강제로 포함시켜 전달
    # 이렇게 하면 "사다리게임" 이라고만 말해도 LLM은 "[coding Context] 사다리게임" 으로 인식합니다.
    display_message = user_message
    if user_subject and str(user_subject).lower() not in ["general", ""]:
        display_message = f"[{user_subject} Context] {user_message}"

    user_obj = {
        "user_message": display_message,
        "intent": intent,
        "slots": slots,
        "meta_summary": { 
            "age": age_group,
            "tone": tone_style,
            "level": level_key,
            "subject": user_subject,
            "exam": target_exam
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
            "age_group": age_group,
            "tone_style": tone_style,
            "level": level_key, 
            "subject": user_subject,
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

    ui_hints["used_pdf_rag"] = bool(pdf_context)

    return {"text": text, "ui_hints": ui_hints}