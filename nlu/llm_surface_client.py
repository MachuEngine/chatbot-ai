# nlu/llm_surface_client.py
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

import requests

try:
    from utils.logging import log_event  # type: ignore
except Exception:  # pragma: no cover
    log_event = None  # type: ignore

OPENAI_API_URL = "https://api.openai.com/v1/responses"


def _enabled() -> bool:
    if os.getenv("OPENAI_ENABLE_LLM", "").strip() != "1":
        return False
    if os.getenv("OPENAI_ENABLE_SURFACE", "1").strip() != "1":
        return False
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def _extract_output_text(resp_json: Dict[str, Any]) -> str:
    if isinstance(resp_json.get("output_text"), str) and resp_json["output_text"].strip():
        return resp_json["output_text"].strip()
    out = resp_json.get("output")
    if isinstance(out, list):
        for item in out:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if isinstance(c, dict) and isinstance(c.get("text"), str) and c["text"].strip():
                    return c["text"].strip()
    return ""


def surface_rewrite(
    *,
    base_text: str,
    facts: Dict[str, Any],
    trace_id: Optional[str] = None,
) -> Optional[str]:
    if not _enabled():
        return None

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_SURFACE_MODEL", os.getenv("OPENAI_NLU_MODEL", "gpt-4o-mini")).strip() or "gpt-4o-mini"

    system = (
        "You are a Korean message rewriter for a transactional assistant. "
        "Rewrite BASE_MESSAGE into natural, polite, concise Korean (1~2 sentences). "
        "Rules:\n"
        "1) DO NOT add any new factual details not present in FACTS or BASE_MESSAGE.\n"
        "2) DO NOT change outcomes implied by BASE_MESSAGE.\n"
        "3) DO NOT mention AI/models/prompts.\n"
    )

    user = (
        f"BASE_MESSAGE:\n{base_text.strip()}\n\n"
        f"FACTS(JSON):\n{json.dumps(facts, ensure_ascii=False)}\n\n"
        "Rewrite BASE_MESSAGE accordingly."
    )

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "store": False,
    }

    t0 = time.perf_counter()
    try:
        r = requests.post(
            OPENAI_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False),
            timeout=15,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:600]}")
        j = r.json()
        text = _extract_output_text(j).strip()
        dt_ms = int((time.perf_counter() - t0) * 1000)

        if log_event and trace_id:
            usage = j.get("usage") if isinstance(j.get("usage"), dict) else {}
            log_event(trace_id, "surface_rewrite_ok", {"model": model, "latency_ms": dt_ms, "usage": usage})

        return text if text else None
    except Exception as e:
        dt_ms = int((time.perf_counter() - t0) * 1000)
        if log_event and trace_id:
            log_event(trace_id, "surface_rewrite_fail", {"model": model, "latency_ms": dt_ms, "error": str(e)})
        return None
