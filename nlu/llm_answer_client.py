# nlu/llm_answer_client.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from utils.logging import log_event

try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore


def _model_for_answer() -> str:
    return (os.getenv("OPENAI_ANSWER_MODEL") or os.getenv("OPENAI_NLU_MODEL") or "gpt-4o-mini").strip()


def _client() -> Any:
    if OpenAI is None:
        raise RuntimeError("openai_sdk_not_installed")

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY_missing")

    return OpenAI(api_key=api_key)


def _safe_str(x: Any) -> str:
    return "" if x is None else str(x)


def _get(slots: Dict[str, Any], key: str) -> Any:
    v = slots.get(key)
    if isinstance(v, dict) and "value" in v:
        return v.get("value")
    return v


def answer_with_openai(
    *,
    user_message: str,
    system_prompt: str = "You are a helpful assistant.",
    model: Optional[str] = None,
    temperature: float = 0.3,
    max_output_tokens: Optional[int] = None,
    trace_id: Optional[str] = None,
) -> str:
    """
    executor.py 호환용 “단순 답변 생성” 함수.
    """
    enable = (os.getenv("OPENAI_ENABLE_LLM") or "").strip() == "1"
    if not enable:
        raise RuntimeError("OPENAI_ENABLE_LLM_disabled")

    m = (model or _model_for_answer()).strip()

    log_event(
        trace_id,
        "llm_answer_call",
        {"model": m, "user_len": len(user_message), "sys_len": len(system_prompt), "temperature": temperature},
    )

    c = _client()

    kwargs: Dict[str, Any] = {}
    if max_output_tokens is not None:
        kwargs["max_tokens"] = int(max_output_tokens)

    resp = c.chat.completions.create(
        model=m,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=float(temperature),
        **kwargs,
    )

    text = resp.choices[0].message.content if resp and resp.choices else ""
    text = (text or "").strip()

    log_event(trace_id, "llm_answer_ok", {"model": m, "text_len": len(text)})
    return text


def generate_text_with_llm(kind: str, slots: Dict[str, Any], trace_id: Optional[str] = None) -> str:
    """
    kind examples:
      - edu_explain_concept
      - edu_ask_question
      - edu_summarize_text
      - edu_give_feedback
      - edu_create_practice
      - edu_check_answer
      - edu_rewrite
    """
    enable = (os.getenv("OPENAI_ENABLE_LLM") or "").strip() == "1"
    if not enable:
        raise RuntimeError("OPENAI_ENABLE_LLM_disabled")

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY_missing")

    model = _model_for_answer()

    # ---- build prompt ----
    kind = (kind or "").strip()

    # 기본 입력들
    question = _safe_str(_get(slots, "question") or _get(slots, "topic") or "")
    topic = _safe_str(_get(slots, "topic") or "")
    content = _safe_str(_get(slots, "content") or "")
    student_answer = _safe_str(_get(slots, "student_answer") or _get(slots, "text") or "")

    system = (
        "너는 한국어 교육 도우미야. "
        "정확하고 짧게 답하고, 필요하면 예시를 1~2개 들어."
    )

    user = ""
    if kind == "edu_explain_concept":
        target = topic.strip() or question.strip()
        user = f"개념을 설명해줘.\n주제: {target}\n조건: 초등~중등 수준으로 6~10문장."
    elif kind == "edu_ask_question":
        user = f"질문에 답해줘.\n질문: {question}\n조건: 핵심부터, 6~10문장."
    elif kind == "edu_summarize_text":
        user = f"다음을 5문장 이내로 요약해줘.\n\n{content}"
    elif kind == "edu_give_feedback":
        user = (
            "학습자 발화/문장에 대한 피드백을 해줘.\n"
            "좋은 점 1~2개, 개선점 1~2개만.\n\n"
            f"{student_answer}"
        )
    elif kind == "edu_create_practice":
        target = topic.strip() or question.strip()
        user = (
            f"연습문제 3개를 만들어줘.\n주제: {target}\n"
            "형식: (1) 객관식 1개 (2) 단답형 1개 (3) 말하기/쓰기 1개"
        )
    elif kind == "edu_check_answer":
        user = (
            "정답 여부를 판단하고 짧게 해설해줘.\n"
            f"문제/질문: {question}\n"
            f"학습자 답: {student_answer}\n"
        )
    elif kind == "edu_rewrite":
        style = _safe_str(_get(slots, "style") or "자연스럽게")
        user = f"다음 문장을 {style} 다시 써줘:\n{question}"
    else:
        raise RuntimeError(f"unsupported_kind:{kind}")

    log_event(
        trace_id,
        "edu_llm_call",
        {
            "model": model,
            "kind": kind,
            "question_len": len(question),
            "content_len": len(content),
            "student_answer_len": len(student_answer),
        },
    )

    # ---- call OpenAI ----
    c = _client()
    resp = c.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
    )

    text = resp.choices[0].message.content if resp and resp.choices else ""
    text = (text or "").strip()

    log_event(trace_id, "edu_llm_ok", {"model": model, "kind": kind, "text_len": len(text)})
    return text


# [추가] response_renderer에서 사용하는 호환용 함수들
def generate_education_answer(question: str, trace_id: Optional[str] = None) -> str:
    return generate_text_with_llm(kind="edu_ask_question", slots={"question": question}, trace_id=trace_id)


def generate_education_summary(content: str, trace_id: Optional[str] = None) -> str:
    return generate_text_with_llm(kind="edu_summarize_text", slots={"content": content}, trace_id=trace_id)