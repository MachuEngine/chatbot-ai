# utils/logging.py
import json
import logging
from typing import Any, Dict

logger = logging.getLogger("chatbot")
logger.setLevel(logging.INFO)

if not logger.handlers:
    h = logging.StreamHandler()
    h.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    h.setFormatter(fmt)
    logger.addHandler(h)

def log_event(trace_id: str, stage: str, payload: Dict[str, Any]):
    # JSON 로그(나중에 ELK/CloudWatch로 보내기 쉬움)
    msg = {"trace_id": trace_id, "stage": stage, **payload}
    logger.info(json.dumps(msg, ensure_ascii=False))
