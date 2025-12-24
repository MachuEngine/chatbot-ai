# nlu/llm_client.py
from __future__ import annotations

import os
import json
from typing import Dict, Any, List, Optional, Set

import requests

try:
    from utils.logging import log_event  # type: ignore
except Exception:  # pragma: no cover
    log_event = None  # type: ignore

from domain.kiosk.schema import KIOSK_SCHEMA
from domain.education.schema import EDUCATION_SCHEMA


def _minimal_fallback_nlu(req) -> Dict[str, Any]:
    msg = (getattr(req, "user_message", "") or "").strip()
    meta = getattr(req, "meta", None)
    mode = (getattr(meta, "mode", "") or "").lower()

    domain = "education" if mode in ("edu", "education") else "kiosk"

    if domain == "education":
        return {
            "domain": "education",
            "intent": "ask_question",
            "intent_confidence": 0.1,
            "slots": {"question": {"value": msg, "confidence": 0.1}},
        }

    return {
        "domain": "kiosk",
        "intent": "fallback",
        "intent_confidence": 0.1,
        "slots": {},
    }


def _schema_for_domain(domain: str) -> Dict[str, Any]:
    d = (domain or "").strip().lower()
    if d == "education":
        return EDUCATION_SCHEMA
    return KIOSK_SCHEMA


def _domains_from_candidates(candidates: List[Dict[str, Any]]) -> List[str]:
    ds: Set[str] = set()
    for c in candidates:
        d = (c.get("domain") or "").strip().lower()
        if d:
            ds.add(d)
    return sorted(ds) or ["kiosk"]


def _intents_from_candidates(candidates: List[Dict[str, Any]]) -> List[str]:
    its: Set[str] = set()
    for c in candidates:
        it = (c.get("intent") or "").strip()
        if it:
            its.add(it)
    return sorted(its) or ["fallback"]


def build_domain_intent_schema(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    domains = _domains_from_candidates(candidates)
    intents = _intents_from_candidates(candidates)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "domain": {"type": "string", "enum": domains},
            "intent": {"type": "string", "enum": intents},
            "intent_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["domain", "intent", "intent_confidence"],
    }


def _intent_slot_names(domain_schema: Dict[str, Any], intent: str) -> List[str]:
    intents = domain_schema.get("intents") or {}
    it = intents.get(intent) or {}
    req = it.get("required_slots") or []
    opt = it.get("optional_slots") or []
    return sorted(set([*req, *opt]))


def build_slots_schema(domain: str, intent: str, domain_schema: Dict[str, Any]) -> Dict[str, Any]:
    slot_names = _intent_slot_names(domain_schema, intent)

    option_group_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "group": {"type": "string"},
            "value": {"type": ["string", "integer", "number", "boolean", "null"]},
        },
        "required": ["group", "value"],
    }

    slot_item_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "enum": slot_names},
            "value_str": {"type": ["string", "null"]},
            "value_int": {"type": ["integer", "null"]},
            "value_num": {"type": ["number", "null"]},
            "value_bool": {"type": ["boolean", "null"]},
            "value_option_groups": {
                "type": ["array", "null"],
                "items": option_group_item,
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "name",
            "value_str", "value_int", "value_num", "value_bool", "value_option_groups",
            "confidence",
        ],
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"slots": {"type": "array", "items": slot_item_schema}},
        "required": ["slots"],
    }


OPENAI_API_URL = "https://api.openai.com/v1/responses"


def _parse_responses_json(resp_json: Dict[str, Any]) -> Dict[str, Any]:
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


def _openai_call_json_schema(
    *,
    model: str,
    system: str,
    user_obj: Dict[str, Any],
    schema_name: str,
    json_schema: Dict[str, Any],
    api_key: str,
    timeout: int = 20,
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
    return _parse_responses_json(r.json())


def _safe_meta_dump(meta: Any) -> Any:
    if meta is None:
        return None
    if hasattr(meta, "model_dump"):
        try:
            return meta.model_dump()
        except Exception:
            return str(meta)
    return str(meta)


def _openai_nlu_two_stage(req, state: Dict[str, Any], candidates: List[Dict[str, Any]], trace_id: Optional[str]) -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is empty")

    model = os.getenv("OPENAI_NLU_MODEL", "gpt-4o-mini").strip()

    msg = (getattr(req, "user_message", "") or "").strip()
    meta = getattr(req, "meta", None)

    base_user = {
        "user_message": msg,
        "meta": _safe_meta_dump(meta),
        "state_summary": {
            "turn_index": state.get("turn_index"),
            "current_domain": state.get("current_domain"),
            "active_intent": state.get("active_intent"),
            "last_bot_action": state.get("last_bot_action"),
            "slots": state.get("slots"),
        },
        "candidates": candidates,
    }

    system1 = (
        "You are an NLU router. "
        "Choose the best (domain, intent) ONLY from the given candidates. "
        "Be conservative. Do not invent new domains or intents."
    )
    schema1 = build_domain_intent_schema(candidates)

    out1 = _openai_call_json_schema(
        model=model,
        system=system1,
        user_obj=base_user,
        schema_name="nlu_route",
        json_schema=schema1,
        api_key=api_key,
        timeout=20,
    )

    domain = (out1.get("domain") or "").strip().lower()
    intent = (out1.get("intent") or "").strip()
    intent_conf = float(out1.get("intent_confidence") or 0.0)

    if log_event and trace_id:
        log_event(trace_id, "nlu_openai_stage1_ok", {
            "model": model,
            "domain": domain,
            "intent": intent,
            "intent_confidence": intent_conf,
        })

    domain_schema = _schema_for_domain(domain)

    if intent not in (domain_schema.get("intents") or {}):
        return {
            "domain": domain or "kiosk",
            "intent": intent or "fallback",
            "intent_confidence": max(min(intent_conf, 1.0), 0.0),
            "slots": {},
        }

    schema2 = build_slots_schema(domain, intent, domain_schema)

    # ✅ intent별 슬롯 가이드 (핵심)
    slot_guidance = {}
    if domain == "kiosk":
        slot_guidance = {
            "RULES": [
                "Do NOT hallucinate menu availability or prices.",
                "If the user mentions temperature preference (아이스/뜨거운/hot/ice):",
                "- If intent is add_item or ask_price: put it into option_groups as {group:'temperature', value:'iced'|'hot'}.",
                "- If intent is ask_recommendation: put it into temperature_hint ('iced'|'hot'), NOT into option_groups.",
                "For 'iced/hot' values, use EXACT strings: 'iced' or 'hot'.",
                "Do not fill both temperature_hint and option_groups.temperature for the same message unless explicitly needed by schema (prefer the rule above).",
            ]
        }

    system2 = (
        "You are an NLU slot extractor.\n"
        "Return slots as an array of objects.\n"
        "IMPORTANT: For each slot item, fill EXACTLY ONE of these fields and set all others to null:\n"
        "- value_str, value_int, value_num, value_bool, value_option_groups\n"
        "Use value_option_groups ONLY when the slot name is 'option_groups'.\n"
        "If unknown, set all value_* fields to null and confidence low.\n"
        "Never invent facts.\n"
        "Follow slot_guidance strictly.\n"
    )

    user2 = {
        **base_user,
        "chosen": {"domain": domain, "intent": intent},
        "slot_spec": {
            "required_slots": (domain_schema.get("intents", {}).get(intent, {}) or {}).get("required_slots") or [],
            "optional_slots": (domain_schema.get("intents", {}).get(intent, {}) or {}).get("optional_slots") or [],
        },
        "slot_guidance": slot_guidance,
    }

    out2 = _openai_call_json_schema(
        model=model,
        system=system2,
        user_obj=user2,
        schema_name="nlu_slots",
        json_schema=schema2,
        api_key=api_key,
        timeout=20,
    )

    raw_slots = out2.get("slots")
    slots: Dict[str, Any] = {}

    def _pick_value(item: Dict[str, Any]):
        if item.get("value_option_groups") is not None:
            return item.get("value_option_groups")
        for k in ("value_str", "value_int", "value_num", "value_bool"):
            if item.get(k) is not None:
                return item.get(k)
        return None

    if isinstance(raw_slots, list):
        for item in raw_slots:
            if not isinstance(item, dict):
                continue

            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue

            try:
                conf_f = float(item.get("confidence", 0.0))
            except Exception:
                conf_f = 0.0

            val = _pick_value(item)

            prev = slots.get(name)
            prev_conf = float(prev.get("confidence", 0.0)) if isinstance(prev, dict) else -1.0
            if conf_f >= prev_conf:
                slots[name] = {"value": val, "confidence": max(min(conf_f, 1.0), 0.0)}
    else:
        slots = {}

    if log_event and trace_id:
        log_event(trace_id, "nlu_openai_stage2_ok", {
            "domain": domain,
            "intent": intent,
            "slots_keys": list(slots.keys()),
        })

    return {
        "domain": domain,
        "intent": intent,
        "intent_confidence": max(min(intent_conf, 1.0), 0.0),
        "slots": slots,
    }


def nlu_with_llm(
    req,
    state: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    msg = (getattr(req, "user_message", "") or "").strip()
    meta = getattr(req, "meta", None)
    mode = (getattr(meta, "mode", "") or "").lower().strip()

    if log_event and trace_id:
        log_event(trace_id, "nlu_enter", {
            "mode": mode,
            "msg_len": len(msg),
            "candidates_count": len(candidates) if isinstance(candidates, list) else None,
            "state_turn_index": state.get("turn_index") if isinstance(state, dict) else None,
        })

    enable_llm = os.getenv("OPENAI_ENABLE_LLM", "").strip() == "1"
    has_key = bool(os.getenv("OPENAI_API_KEY", "").strip())

    if enable_llm and has_key:
        try:
            out = _openai_nlu_two_stage(req, state, candidates, trace_id)
            if log_event and trace_id:
                log_event(trace_id, "nlu_exit", {
                    "provider": "openai",
                    "domain": out.get("domain"),
                    "intent": out.get("intent"),
                    "intent_confidence": out.get("intent_confidence"),
                    "slots_keys": list((out.get("slots") or {}).keys()) if isinstance(out.get("slots"), dict) else [],
                })
            return out
        except Exception as e:
            if log_event and trace_id:
                log_event(trace_id, "nlu_openai_fail", {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                })

    out = _minimal_fallback_nlu(req)
    if log_event and trace_id:
        log_event(trace_id, "nlu_exit", {
            "provider": "fallback",
            "domain": out.get("domain"),
            "intent": out.get("intent"),
            "intent_confidence": out.get("intent_confidence"),
            "slots_keys": list((out.get("slots") or {}).keys()) if isinstance(out.get("slots"), dict) else [],
        })
    return out
