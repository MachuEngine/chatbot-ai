# nlu/llm_client.py
from __future__ import annotations

import os
import json
import re
from typing import Dict, Any, List, Optional, Set, Tuple

import requests

try:
    from utils.logging import log_event  # type: ignore
except Exception:  # pragma: no cover
    log_event = None  # type: ignore

from domain import SCHEMAS


def _minimal_fallback_nlu(req) -> Dict[str, Any]:
    msg = (getattr(req, "user_message", "") or "").strip()
    meta = getattr(req, "meta", None)
    mode = (getattr(meta, "mode", "") or "").lower()

    domain = "education" if mode in ("edu", "education") else "kiosk"

    if domain == "education":
        return {
            "domain": "education",
            "intent": "ask_knowledge",
            "intent_confidence": 0.1,
            "slots": {"topic": {"value": msg, "confidence": 0.1}},
        }

    return {
        "domain": "kiosk",
        "intent": "fallback",
        "intent_confidence": 0.1,
        "slots": {},
    }


def _schema_for_domain(domain: str) -> Dict[str, Any]:
    d = (domain or "").strip().lower()
    return SCHEMAS.get(d, SCHEMAS.get("kiosk", {}))


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
            "reasoning": {
                "type": "string",
                "description": "Brief reasoning for the chosen domain and intent."
            },
            "domain": {"type": "string", "enum": domains},
            "intent": {"type": "string", "enum": intents},
            "intent_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["reasoning", "domain", "intent", "intent_confidence"],
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
            "value_str",
            "value_int",
            "value_num",
            "value_bool",
            "value_option_groups",
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


def _norm_kiosk_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^a-z0-9가-힣]", "", s)
    return s


def _heuristic_kiosk_option_groups(msg: str) -> Dict[str, str]:
    m = _norm_kiosk_text(msg)
    out: Dict[str, str] = {}

    if any(k in m for k in ["아이스", "ice", "iced", "차가", "시원"]):
        out["temperature"] = "ice"
    elif any(k in m for k in ["뜨거", "핫", "hot", "따뜻"]):
        out["temperature"] = "hot"

    if any(k in m for k in ["라지", "large", "l사이즈", "lsize", "제일큰", "가장큰", "큰"]):
        out["size"] = "L"
    elif any(k in m for k in ["미디움", "medium", "m사이즈", "msize", "중간", "보통"]):
        out["size"] = "M"
    elif any(k in m for k in ["스몰", "small", "s사이즈", "ssize", "제일작", "가장작", "작은"]):
        out["size"] = "S"

    if "size" not in out:
        if re.search(r"(^|[^a-z0-9])l($|[^a-z0-9])", (msg or "").strip(), flags=re.IGNORECASE):
            out["size"] = "L"
        elif re.search(r"(^|[^a-z0-9])m($|[^a-z0-9])", (msg or "").strip(), flags=re.IGNORECASE):
            out["size"] = "M"
        elif re.search(r"(^|[^a-z0-9])s($|[^a-z0-9])", (msg or "").strip(), flags=re.IGNORECASE):
            out["size"] = "S"

    return out


def _merge_option_groups_list(
    existing: Any,
    add: Dict[str, str],
) -> List[Dict[str, Any]]:
    lst: List[Dict[str, Any]] = []
    if isinstance(existing, list):
        for it in existing:
            if isinstance(it, dict) and isinstance(it.get("group"), str):
                lst.append({"group": it["group"], "value": it.get("value")})

    seen = {it["group"]: True for it in lst if isinstance(it, dict) and "group" in it}

    for g, v in add.items():
        if g not in seen:
            lst.append({"group": g, "value": v})
            seen[g] = True

    return lst


def _openai_nlu_two_stage(
    req,
    state: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    trace_id: Optional[str],
) -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is empty")

    model = os.getenv("OPENAI_NLU_MODEL", "gpt-4o-mini").strip()

    msg = (getattr(req, "user_message", "") or "").strip()
    meta = getattr(req, "meta", None)

    edu_payload = {
        "content": getattr(req, "content", None),
        "student_answer": getattr(req, "student_answer", None),
        "topic": getattr(req, "topic", None),
    }

    base_user = {
        "user_message": msg,
        "meta": _safe_meta_dump(meta),
        "edu_payload": edu_payload,
        "state_summary": {
            "turn_index": state.get("turn_index"),
            "current_domain": state.get("current_domain"),
            "active_intent": state.get("active_intent"),
            "last_bot_action": state.get("last_bot_action"),
            "pending_option_group": state.get("pending_option_group"),
            "pending_option_group_choices": state.get("pending_option_group_choices"),
            "slots": state.get("slots"),
        },
        "candidates": candidates,
    }

    # --- Stage 1: Router ---
    system1 = (
        "You are an NLU router.\n"
        "Analyze the user's message and context, then select the best (domain, intent) from the candidates.\n"
        "Provide brief reasoning."
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
    reasoning = out1.get("reasoning", "")

    if log_event and trace_id:
        log_event(trace_id, "nlu_openai_stage1_ok", {"domain": domain, "intent": intent, "reasoning": reasoning})

    domain_schema = _schema_for_domain(domain)
    if intent not in (domain_schema.get("intents") or {}):
        return {
            "domain": domain or "kiosk",
            "intent": intent or "fallback",
            "intent_confidence": max(min(intent_conf, 1.0), 0.0),
            "slots": {},
        }

    # --- Stage 2: Slot Extraction ---
    slot_guidance: Dict[str, Any] = {}

    if domain == "kiosk":
        slot_guidance = {
            "RULES": [
                "1. Item Name: Extract ONLY the menu name. Exclude temperature (ice/hot) and size (S/M/L).",
                "2. Options: Extract temperature and size into 'option_groups'.",
                "3. Context: If answering a pending option question, prioritize the user's answer for that option.",
                "4. No History: Extract slots ONLY from the current user_message."
            ]
        }
    elif domain == "education":
        slot_guidance = {
            "RULES": [
                # ✅ [수정] 범용 주제 추출 가이드
                "1. TOPIC: Extract the main subject or concept the user wants to learn (e.g., 'Quantum Mechanics', 'French Revolution', 'Subjunctive Mood').",
                "2. NO GENERIC TOPICS: Do NOT extract generic words (e.g., 'explain', 'help', 'question') as 'topic'. Use 'request_type' instead.",
                "3. CONTEXT: If the topic is unclear, leave it NULL (system will use history).",
                "4. PAYLOAD: Use edu_payload fields if present."
            ]
        }

    system2 = (
        "You are an NLU slot extractor.\n"
        "Extract slots from the current 'user_message' based on the schema.\n"
        "\n"
        "GUIDELINES:\n"
        "1. Focus ONLY on the current message. Do NOT copy slots from history.\n"
        "2. For each slot, fill exactly one value field and leave others null.\n"
        "3. Follow the 'slot_guidance' rules strictly, especially for 'topic' constraints.\n"
        f"Context Reasoning: {reasoning}\n"
    )

    schema2 = build_slots_schema(domain, intent, domain_schema)
    
    # ✅ [유지] Stage 2는 Stateless로 동작 (Phantom Slot 방지)
    user2 = {
        "user_message": msg,
        "meta": _safe_meta_dump(meta),
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
            if not isinstance(item, dict): continue
            name = item.get("name")
            if not isinstance(name, str) or not name: continue

            try: conf_f = float(item.get("confidence", 0.0))
            except Exception: conf_f = 0.0
            
            val = _pick_value(item)
            prev = slots.get(name)
            prev_conf = float(prev.get("confidence", 0.0)) if isinstance(prev, dict) else -1.0
            
            if conf_f >= prev_conf:
                slots[name] = {"value": val, "confidence": max(min(conf_f, 1.0), 0.0)}

    # Kiosk Heuristic (Option Groups)
    if domain == "kiosk" and intent in ("add_item", "ask_price"):
        og = slots.get("option_groups")
        og_val = og.get("value") if isinstance(og, dict) else None
        heur = _heuristic_kiosk_option_groups(msg)
        
        if heur:
            merged_list = _merge_option_groups_list(og_val, heur)
            if merged_list:
                prev_conf = float(og.get("confidence", 0.0)) if isinstance(og, dict) else 0.0
                slots["option_groups"] = {"value": merged_list, "confidence": max(prev_conf, 0.55)}
                if log_event and trace_id:
                    log_event(trace_id, "nlu_kiosk_heuristic_ok", {"merged": merged_list})

    if log_event and trace_id:
        log_event(trace_id, "nlu_openai_stage2_ok", {"domain": domain, "intent": intent, "slots_keys": list(slots.keys())})

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
    # (LLM Enabled 체크 및 에러 핸들링은 기존 유지)
    enable_llm = os.getenv("OPENAI_ENABLE_LLM", "").strip() == "1"
    has_key = bool(os.getenv("OPENAI_API_KEY", "").strip())

    if enable_llm and has_key:
        try:
            return _openai_nlu_two_stage(req, state, candidates, trace_id)
        except Exception as e:
            if log_event and trace_id:
                log_event(trace_id, "nlu_openai_fail", {"error": str(e)})

    return _minimal_fallback_nlu(req)