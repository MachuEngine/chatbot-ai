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

# ✅ 변경: 개별 import 대신 domain 패키지의 통합 SCHEMAS 사용
from domain import SCHEMAS


def _minimal_fallback_nlu(req) -> Dict[str, Any]:
    msg = (getattr(req, "user_message", "") or "").strip()
    meta = getattr(req, "meta", None)
    mode = (getattr(meta, "mode", "") or "").lower()

    domain = "education" if mode in ("edu", "education") else "kiosk"

    if domain == "education":
        return {
            "domain": "education",
            "intent": "ask_knowledge",  # 변경된 기본 인텐트
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
    # ✅ 변경: 동적 로딩된 SCHEMAS에서 조회, 없으면 kiosk(기본값)
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
    
    # ✅ 개선: reasoning 필드 추가 (CoT 적용)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "Briefly explain why you chose this domain and intent based on user input and context."
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


# ----------------------------
# kiosk 옵션 휴리스틱(핵심)
# ----------------------------

def _norm_kiosk_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^a-z0-9가-힣]", "", s)
    return s


def _heuristic_kiosk_option_groups(msg: str) -> Dict[str, str]:
    """
    LLM이 size/temperature를 놓치는 경우를 커버.
    - 반환: {"temperature":"ice"|"hot", "size":"S"|"M"|"L"} 일부만 들어올 수 있음
    """
    m = _norm_kiosk_text(msg)
    out: Dict[str, str] = {}

    # temperature
    if any(k in m for k in ["아이스", "ice", "iced", "차가", "시원"]):
        out["temperature"] = "ice"
    elif any(k in m for k in ["뜨거", "핫", "hot", "따뜻"]):
        out["temperature"] = "hot"

    # size
    # (주의) "라지사이즈"처럼 붙는 케이스가 많아서 공백 제거된 m 기준
    if any(k in m for k in ["라지", "large", "l사이즈", "lsize", "제일큰", "가장큰", "큰"]):
        out["size"] = "L"
    elif any(k in m for k in ["미디움", "medium", "m사이즈", "msize", "중간", "보통"]):
        out["size"] = "M"
    elif any(k in m for k in ["스몰", "small", "s사이즈", "ssize", "제일작", "가장작", "작은"]):
        out["size"] = "S"

    # 단일 문자만 말한 경우도 커버 (예: "l로", "L", "엠")
    # 공백 제거라서 "l로" 같은 것도 잡힘
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
    """
    existing: [{"group":"size","value":"L"}, ...] or None
    add: {"size":"L", "temperature":"ice"}
    => merged list (group unique)
    """
    lst: List[Dict[str, Any]] = []
    if isinstance(existing, list):
        for it in existing:
            if isinstance(it, dict) and isinstance(it.get("group"), str):
                lst.append({"group": it["group"], "value": it.get("value")})

    # existing groups
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

    mode = (getattr(meta, "mode", "") or "").lower().strip()
    
    # ✅ 개선: 1단계 프롬프트에 CoT 요청 추가
    system1 = (
        "You are an NLU router.\n"
        "You should use a friendly and gentle tone of voice.\n"
        "1. First, analyze the user's message and context. Write your thoughts briefly in the 'reasoning' field.\n"
        "2. Then, choose the best (domain, intent) ONLY from the given candidates.\n"
        "Be conservative. Do not invent new domains or intents."
    )
    if mode in ("edu", "education"):
        system1 += (
            "\nIMPORTANT: The client is in EDU mode. "
            "You must only respond for the purpose of learning Korean."
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
    reasoning = out1.get("reasoning", "") # ✅ 추출된 추론 내용

    if log_event and trace_id:
        log_event(
            trace_id,
            "nlu_openai_stage1_ok",
            {
                "model": model,
                "domain": domain,
                "intent": intent,
                "intent_confidence": intent_conf,
                "reasoning": reasoning,
            },
        )

    domain_schema = _schema_for_domain(domain)

    if intent not in (domain_schema.get("intents") or {}):
        return {
            "domain": domain or "kiosk",
            "intent": intent or "fallback",
            "intent_confidence": max(min(intent_conf, 1.0), 0.0),
            "slots": {},
        }

    schema2 = build_slots_schema(domain, intent, domain_schema)

    slot_guidance: Dict[str, Any] = {}

    if domain == "kiosk":
        slot_guidance = {
            "RULES": [
                "Do NOT hallucinate menu availability or prices.",
                "Extract slots ONLY from the current user_message. Do NOT copy slots from state_summary.",
                "If a value is not explicitly present in the current user_message, leave it null with low confidence.",
                "",
                "CRITICAL: item_name MUST be the menu name ONLY.",
                "- NEVER include temperature words in item_name (e.g., 아이스/뜨거운/hot/ice/iced).",
                "- NEVER include size words in item_name (e.g., 라지/스몰/미디움/큰/작은/S/M/L/사이즈).",
                "- Example: '아메리카노 아이스로 라지' => item_name='아메리카노', option_groups=[{temperature:'ice'},{size:'L'}]",
                "",
                "If state_summary.last_bot_action is 'ask_option_group' and state_summary.pending_option_group exists:",
                "- Treat the user's message as an answer to that pending option question (highest priority).",
                "- Extract the pending option group if it is explicitly mentioned OR can be mapped from the message.",
                "- ALSO extract other option groups (e.g., temperature/size) IF they are explicitly mentioned in the same user_message.",
                "- Do NOT infer any option values from previous turns if the current message doesn't mention them.",
                "- Do NOT fill item_name/quantity unless the current message explicitly contains a NEW order (menu name or clear new order request).",
                "",
                "Temperature rule:",
                "- If the user mentions temperature preference (아이스/차가운/뜨거운/hot/ice):",
                "  - If intent is add_item or ask_price: put it into option_groups as {group:'temperature', value:'ice'|'hot'}.",
                "  - If intent is ask_recommendation: put it into temperature_hint ('ice'|'hot'), NOT into option_groups.",
                "- For temperature values, use EXACT strings: 'ice' or 'hot'.",
                "",
                "Size rule (when relevant):",
                "- Map '제일 작은/가장 작은/작은/스몰/s/small' => 'S'",
                "- Map '중간/보통/m/medium' => 'M'",
                "- Map '제일 큰/가장 큰/큰/라지/l/large' => 'L'",
                "- Use EXACT uppercase: 'S','M','L'.",
            ]
        }
    elif domain == "education":
        slot_guidance = {
            "RULES": [
                "Use edu_payload fields if present.",
                "If edu_payload.content is provided and slot name 'content' exists, use it for that slot.",
                "If edu_payload.student_answer is provided and slot name 'student_answer' exists, use it for that slot.",
                "If edu_payload.topic is provided and slot name 'topic' exists, use it for that slot.",
                "Do NOT hallucinate missing text. If the user requests summarization/rewriting but content is missing, leave content null and keep confidence low.",
                "Do NOT invent facts.",
                "If the user asks to evaluate/grade feedback and the answer text is included in user_message, extract that part into slot 'student_answer'.",
                "If the user asks to summarize/rewrite/expand and the source text is included in user_message, extract that part into slot 'content'.",
                "If the user explicitly mentions a topic word (e.g., '연음', '받침', '동화'), extract it into slot 'topic'.",
                "If unsure, keep value_* as null with low confidence.",
            ]
        }

    # ✅ 개선: 2단계 프롬프트에 1단계의 추론(reasoning)을 맥락으로 제공
    system2 = (
        "You are an NLU slot extractor.\n"
        "Return slots as an array of objects.\n"
        "IMPORTANT: For each slot item, fill EXACTLY ONE of these fields and set all others to null:\n"
        "- value_str, value_int, value_num, value_bool, value_option_groups\n"
        "Use value_option_groups ONLY when the slot name is 'option_groups'.\n"
        "If unknown, set all value_* fields to null and confidence low.\n"
        "Never invent facts.\n"
        "Follow slot_guidance strictly.\n"
        f"Context Reasoning from Router: {reasoning}\n"
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

    # ✅ (핵심) kiosk/add_item/ask_price에서 option_groups 후처리 보강
    if domain == "kiosk" and intent in ("add_item", "ask_price"):
        og = slots.get("option_groups")
        og_val = og.get("value") if isinstance(og, dict) else None

        heur = _heuristic_kiosk_option_groups(msg)
        if heur:
            merged_list = _merge_option_groups_list(og_val, heur)
            if merged_list:
                # confidence는 LLM보다 낮게(후처리니까)
                prev_conf = float(og.get("confidence", 0.0)) if isinstance(og, dict) else 0.0
                slots["option_groups"] = {
                    "value": merged_list,
                    "confidence": max(prev_conf, 0.55),
                }

                if log_event and trace_id:
                    log_event(
                        trace_id,
                        "nlu_kiosk_option_groups_heuristic_merge",
                        {
                            "intent": intent,
                            "heuristic": heur,
                            "merged": merged_list,
                        },
                    )

    if log_event and trace_id:
        log_event(
            trace_id,
            "nlu_openai_stage2_ok",
            {
                "domain": domain,
                "intent": intent,
                "slots_keys": list(slots.keys()),
            },
        )

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
        log_event(
            trace_id,
            "nlu_enter",
            {
                "mode": mode,
                "msg_len": len(msg),
                "candidates_count": len(candidates) if isinstance(candidates, list) else None,
                "state_turn_index": state.get("turn_index") if isinstance(state, dict) else None,
            },
        )

    enable_llm = os.getenv("OPENAI_ENABLE_LLM", "").strip() == "1"
    has_key = bool(os.getenv("OPENAI_API_KEY", "").strip())

    if enable_llm and has_key:
        try:
            out = _openai_nlu_two_stage(req, state, candidates, trace_id)
            if log_event and trace_id:
                log_event(
                    trace_id,
                    "nlu_exit",
                    {
                        "provider": "openai",
                        "domain": out.get("domain"),
                        "intent": out.get("intent"),
                        "intent_confidence": out.get("intent_confidence"),
                        "slots_keys": list((out.get("slots") or {}).keys())
                        if isinstance(out.get("slots"), dict)
                        else [],
                    },
                )
            return out
        except Exception as e:
            if log_event and trace_id:
                log_event(
                    trace_id,
                    "nlu_openai_fail",
                    {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    },
                )

    out = _minimal_fallback_nlu(req)
    if log_event and trace_id:
        log_event(
            trace_id,
            "nlu_exit",
            {
                "provider": "fallback",
                "domain": out.get("domain"),
                "intent": out.get("intent"),
                "intent_confidence": out.get("intent_confidence"),
                "slots_keys": list((out.get("slots") or {}).keys()) if isinstance(out.get("slots"), dict) else [],
            },
        )
    return out