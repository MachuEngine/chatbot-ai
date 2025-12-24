# nlu/router.py
from __future__ import annotations

from typing import Dict, Any, List, Optional, Iterable
from domain import SCHEMAS


def _iter_schemas() -> Iterable[Dict[str, Any]]:
    """
    SCHEMAS가 dict이든 list이든 schema dict들을 순회 가능하게 만든다.
    기대 schema 형태:
      {
        "domain": "kiosk",
        "intents": { "add_item": {...}, ... }
      }
    """
    if isinstance(SCHEMAS, dict):
        for _, sch in SCHEMAS.items():
            if isinstance(sch, dict):
                yield sch
    elif isinstance(SCHEMAS, list):
        for sch in SCHEMAS:
            if isinstance(sch, dict):
                yield sch


def _get_schema_by_domain(domain: str) -> Optional[Dict[str, Any]]:
    domain = (domain or "").strip().lower()
    if not domain:
        return None

    # 1) SCHEMAS가 {"kiosk": schema} 형태면 key로도 먼저 찾아본다.
    if isinstance(SCHEMAS, dict):
        # key 매칭
        direct = SCHEMAS.get(domain)
        if isinstance(direct, dict):
            return direct

    # 2) schema 내부 "domain" 필드로 찾기
    for sch in _iter_schemas():
        sch_domain = (sch.get("domain") or "").strip().lower()
        if sch_domain == domain:
            return sch
    return None


def _schema_to_candidates(schema: Dict[str, Any], domain_override: Optional[str] = None) -> List[Dict[str, str]]:
    """
    schema["intents"] 키들을 candidates로 변환.
    """
    if not isinstance(schema, dict):
        return []
    domain = (domain_override or schema.get("domain") or "").strip().lower()
    intents = schema.get("intents") or {}
    if not domain or not isinstance(intents, dict):
        return []

    return [{"domain": domain, "intent": intent_name} for intent_name in intents.keys()]


def pick_candidates(req, state: Dict[str, Any]) -> List[Dict[str, str]]:
    mode = (getattr(req.meta, "mode", "") or "").lower().strip()

    # mode 정규화: edu/education 통일
    if mode in ("edu", "education"):
        schema = _get_schema_by_domain("education")
        # education 스키마가 없으면 안전 fallback
        return _schema_to_candidates(schema, "education") if schema else [
            {"domain": "education", "intent": "ask_question"},
            {"domain": "education", "intent": "summarize_text"},
        ]

    # kiosk
    if mode == "kiosk" or not mode:
        schema = _get_schema_by_domain("kiosk")
        cands = _schema_to_candidates(schema, "kiosk") if schema else [
            {"domain": "kiosk", "intent": "add_item"},
            {"domain": "kiosk", "intent": "ask_store_info"},
        ]

        # mode가 아예 없으면, 원하면 education을 보조 후보로 붙일 수 있음(주석 해제)
        # if not mode:
        #     edu_schema = _get_schema_by_domain("education")
        #     cands += _schema_to_candidates(edu_schema, "education") if edu_schema else []
        return cands

    # 그 외 mode: 해당 도메인 스키마가 있으면 거기서 뽑고, 없으면 kiosk로 fallback
    schema = _get_schema_by_domain(mode)
    if schema:
        return _schema_to_candidates(schema, mode)

    # unknown mode fallback
    fallback_schema = _get_schema_by_domain("kiosk")
    return _schema_to_candidates(fallback_schema, "kiosk") if fallback_schema else [
        {"domain": "kiosk", "intent": "add_item"},
        {"domain": "kiosk", "intent": "ask_store_info"},
    ]
