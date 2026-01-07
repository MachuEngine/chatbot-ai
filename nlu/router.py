# nlu/router.py
from __future__ import annotations

from typing import Dict, Any, List, Optional, Iterable
from domain import SCHEMAS


def _iter_schemas() -> Iterable[Dict[str, Any]]:
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

    if isinstance(SCHEMAS, dict):
        direct = SCHEMAS.get(domain)
        if isinstance(direct, dict):
            return direct

    for sch in _iter_schemas():
        sch_domain = (sch.get("domain") or "").strip().lower()
        if sch_domain == domain:
            return sch
    return None


def _schema_to_candidates(schema: Dict[str, Any], domain_override: Optional[str] = None) -> List[Dict[str, str]]:
    if not isinstance(schema, dict):
        return []
    domain = (domain_override or schema.get("domain") or "").strip().lower()
    intents = schema.get("intents") or {}
    if not domain or not isinstance(intents, dict):
        return []

    return [{"domain": domain, "intent": intent_name} for intent_name in intents.keys()]


def pick_candidates(req, state: Dict[str, Any]) -> List[Dict[str, str]]:
    mode = (getattr(req.meta, "mode", "") or "").lower().strip()

    # [Companion Mode] - New
    if mode == "companion":
        return [{"domain": "companion", "intent": "general_chat"}]

    # [Driving Mode]
    if mode == "driving":
        schema = _get_schema_by_domain("driving")
        if schema:
            return _schema_to_candidates(schema, "driving")
        
        return [
            {"domain": "driving", "intent": "navigate_to"},
            {"domain": "driving", "intent": "control_hvac"},
            {"domain": "driving", "intent": "control_hardware"},
            {"domain": "driving", "intent": "general_chat"},
        ]

    # [Education Mode]
    if mode in ("edu", "education"):
        schema = _get_schema_by_domain("education")
        return _schema_to_candidates(schema, "education") if schema else [
            {"domain": "education", "intent": "ask_question"},
            {"domain": "education", "intent": "summarize_text"},
        ]

    # [Kiosk Mode] (Default)
    if mode == "kiosk" or not mode:
        schema = _get_schema_by_domain("kiosk")
        cands = _schema_to_candidates(schema, "kiosk") if schema else [
            {"domain": "kiosk", "intent": "add_item"},
            {"domain": "kiosk", "intent": "ask_store_info"},
        ]
        return cands

    # [Other Modes]
    schema = _get_schema_by_domain(mode)
    if schema:
        return _schema_to_candidates(schema, mode)

    # [Fallback]
    fallback_schema = _get_schema_by_domain("kiosk")
    return _schema_to_candidates(fallback_schema, "kiosk") if fallback_schema else [
        {"domain": "kiosk", "intent": "add_item"},
        {"domain": "kiosk", "intent": "ask_store_info"},
    ]