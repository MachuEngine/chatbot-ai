# utils/logging.py
from __future__ import annotations

import json
import logging
from typing import Any, Dict

SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "api_key",
    "openai_api_key",
    "password",
    "secret",
    "cookie",
}

MAX_STR = 800          # 문자열 최대 길이
MAX_LIST = 50          # 리스트 최대 길이
MAX_DICT_KEYS = 80     # dict key 최대 개수


def _truncate_str(s: str) -> str:
    if len(s) <= MAX_STR:
        return s
    return s[:MAX_STR] + "...(truncated)"


def _sanitize(obj: Any, depth: int = 0) -> Any:
    """
    JSON 직렬화 가능 + 로그 폭주 방지 + 민감키 마스킹.
    """
    if depth > 6:
        return "...(max_depth)"

    if obj is None:
        return None

    # 기본 타입
    if isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return _truncate_str(obj)

    # bytes
    if isinstance(obj, (bytes, bytearray)):
        return f"<bytes:{len(obj)}>"

    # dict
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        keys = list(obj.keys())
        if len(keys) > MAX_DICT_KEYS:
            keys = keys[:MAX_DICT_KEYS]
            out["_truncated_keys"] = True

        for k in keys:
            lk = str(k).lower()
            if lk in SENSITIVE_KEYS:
                out[k] = "***"
            else:
                out[k] = _sanitize(obj.get(k), depth + 1)
        return out

    # list/tuple/set
    if isinstance(obj, (list, tuple, set)):
        lst = list(obj)
        truncated = False
        if len(lst) > MAX_LIST:
            lst = lst[:MAX_LIST]
            truncated = True
        out_list = [_sanitize(x, depth + 1) for x in lst]
        if truncated:
            out_list.append("...(truncated)")
        return out_list

    # pydantic model 같은 경우
    if hasattr(obj, "model_dump"):
        try:
            return _sanitize(obj.model_dump(), depth + 1)
        except Exception:
            return str(obj)

    # Exception
    if isinstance(obj, Exception):
        return {"error_type": type(obj).__name__, "error_message": _truncate_str(str(obj))}

    # fallback
    return _truncate_str(str(obj))


# logger setup
logger = logging.getLogger("chatbot")
logger.setLevel(logging.INFO)
logger.propagate = False  # ✅ 중복 출력 방지

if not logger.handlers:
    h = logging.StreamHandler()
    h.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    h.setFormatter(fmt)
    logger.addHandler(h)


def log_event(trace_id: str, stage: str, payload: Dict[str, Any]) -> None:
    """
    JSON 구조화 로그. (ELK/CloudWatch friendly)
    """
    msg = {
        "trace_id": trace_id,
        "stage": stage,
        "payload": _sanitize(payload),
    }
    logger.info(json.dumps(msg, ensure_ascii=False))
