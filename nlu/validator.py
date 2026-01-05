# nlu/validator.py
from __future__ import annotations

import re
from typing import Any, Dict, Optional, List, Tuple

from utils.logging import log_event
from domain.kiosk.catalog_repo import CatalogRepo
from domain.kiosk.policy import (
    get_required_option_groups_for_add_item,
    find_missing_required_option_group,
    default_catalog_repo,
)
# [추가] Driving Policy import
from domain.driving.policy import build_vehicle_command, check_action_validity

TEMPLATES = {
    "result.kiosk.add_item": "",
    "result.fail.generic": "",
}


def _slot_value(slots: Dict[str, Any], key: str) -> Any:
    v = slots.get(key)
    if isinstance(v, dict):
        return v.get("value")
    return v


def _merge_state(state: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    new_state = dict(state or {})
    new_state.update(patch or {})
    new_state["turn_index"] = int(new_state.get("turn_index", 0)) + 1
    return new_state


def _normalize_option_groups(option_groups: Any) -> Dict[str, Any]:
    if option_groups is None: return {}
    if isinstance(option_groups, dict):
        v = option_groups.get("value")
        if isinstance(v, dict): return v
        return option_groups
    if isinstance(option_groups, list):
        out: Dict[str, Any] = {}
        for it in option_groups:
            if not isinstance(it, dict): continue
            g = it.get("group")
            v = it.get("value")
            if isinstance(g, str) and g.strip():
                out[g.strip()] = v
        return out
    return {}


def _normalize_temperature_value(v: Any) -> Any:
    if not isinstance(v, str): return v
    s = v.strip().lower()
    if s in {"iced", "ice", "아이스", "차가운", "차가움", "차가운거", "차가운걸"}: return "ice"
    if s in {"hot", "뜨거운", "뜨거움", "핫", "따뜻한", "따뜻한거", "따뜻한걸", "따듯", "따듯한", "따듯한거", "따듯한걸"}: return "hot"
    return v


_ITEM_NOISE_PATTERNS: List[Tuple[str, str]] = [
    (r"(아이스|차가운(거|걸)?|시원한(거|걸)?|iced|ice)\b", " "),
    (r"(뜨거운(거|걸)?|따뜻한(거|걸)?|따듯(한)?(거|걸)?|hot|핫)\b", " "),
    (r"(스몰|small|미디움|medium|라지|large)\b", " "),
    (r"\b(S|M|L)\b", " "),
    (r"(작은(거|걸)?|중간(거|걸)?|큰(거|걸)?|보통(거|걸)?)\b", " "),
    (r"(사이즈|size)\b", " "),
    (r"(두\s*개|2\s*개|두\s*잔|2\s*잔)\b", " "),
    (r"(한\s*개|1\s*개|한\s*잔|1\s*잔)\b", " "),
    (r"(세\s*개|3\s*개|세\s*잔|3\s*잔)\b", " "),
    (r"(주세요|주문|부탁|할게(요)?|줘|주실래요|좀)\b", " "),
]
_TEMP_WORDS = ["아이스", "차가운", "시원한", "iced", "ice", "뜨거운", "따뜻한", "따듯", "hot", "핫"]
_SIZE_WORDS = ["스몰", "small", "미디움", "medium", "라지", "large", "작은", "중간", "큰", "보통", "S", "M", "L"]


def _compact_spaces(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s.strip()


def _recover_item_name_candidates(item_name: Any, option_groups: Dict[str, Any]) -> List[str]:
    if not isinstance(item_name, str): return []
    raw = _compact_spaces(item_name)
    if not raw: return []
    cands: List[str] = [raw]
    s = raw
    for pat, rep in _ITEM_NOISE_PATTERNS:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    s = _compact_spaces(s)
    if s and s not in cands: cands.append(s)
    s2 = raw
    if option_groups.get("temperature") is not None:
        for w in _TEMP_WORDS: s2 = re.sub(rf"\b{re.escape(w)}\b", " ", s2, flags=re.IGNORECASE)
    if option_groups.get("size") is not None:
        for w in _SIZE_WORDS: s2 = re.sub(rf"\b{re.escape(w)}\b", " ", s2, flags=re.IGNORECASE)
    s2 = _compact_spaces(s2)
    if s2 and s2 not in cands: cands.append(s2)
    out: List[str] = []
    for x in cands:
        if len(x) >= 2: out.append(x)
    return out


def _edu_make_llm_task(*, intent: str, slots: Dict[str, Any], meta: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    safe_state = {
        "conversation_id": state.get("conversation_id"),
        "turn_index": state.get("turn_index"),
        "history_summary": state.get("history_summary", ""),
        "active_intent": state.get("active_intent"),
        "slots": state.get("slots", {}),
        "last_bot_action": state.get("last_bot_action"),
    }
    safe_meta = {
        "locale": meta.get("locale"),
        "timezone": meta.get("timezone"),
        "device_type": meta.get("device_type"),
        "mode": meta.get("mode"),
        "input_type": meta.get("input_type"),
    }
    return {
        "type": "edu_answer_generation",
        "input": {"intent": intent, "slots": slots or {}, "meta": safe_meta, "state": safe_state},
        "output_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
                "ui_hints": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"domain": {"type": "string"}, "intent": {"type": "string"}},
                    "required": ["domain", "intent"],
                },
            },
            "required": ["text", "ui_hints"],
        },
    }


def validate_and_build_action(
    *,
    domain: str,
    intent: str,
    slots: Dict[str, Any],
    meta: Dict[str, Any],
    state: Dict[str, Any],
    trace_id: Optional[str] = None,
    catalog: Optional[CatalogRepo] = None,
):
    message_key_ok = f"result.{domain}.{intent}"
    message_key_fail = "result.fail.generic"

    # [Driving Domain]
    if domain == "driving":
        # 1. 상태 충돌 검사
        vehicle_status = meta.get("vehicle_status") or {}
        conflict_reason = check_action_validity(intent, slots, vehicle_status)

        if conflict_reason:
            # 2-A. 이미 완료된 상태 -> Command 없이 피드백만
            action = {
                "reply": {
                    "action_type": "answer", # vehicle_action 아님
                    "text": "이미 처리되어 있습니다.", # Fallback, LLM이 덮어씌움
                    "message_key": f"result.driving.conflict.{conflict_reason}", 
                    "ui_hints": {"domain": domain, "intent": intent, "status": "conflict"},
                }
            }
            new_state = _merge_state(state, {"last_bot_action": "conflict_feedback"})
            return action, new_state

        # 2-B. 정상 실행
        command_payload = build_vehicle_command(intent, slots)
        action = {
            "reply": {
                "action_type": "vehicle_action",
                "text": "처리하겠습니다.", 
                "command": command_payload,
                "ui_hints": {"domain": domain, "intent": intent},
                "message_key_ok": message_key_ok,
            }
        }
        new_state = _merge_state(
            state, 
            {
                "current_domain": domain, 
                "active_intent": intent,
                "slots": {},
                "last_bot_action": intent
            }
        )
        return action, new_state

    # [Kiosk / Add Item]
    if domain == "kiosk" and intent == "add_item":
        item_name = _slot_value(slots, "item_name")
        quantity = _slot_value(slots, "quantity") or 1
        option_groups_raw = _slot_value(slots, "option_groups")
        option_groups = _normalize_option_groups(option_groups_raw)
        if "temperature" in option_groups:
            option_groups["temperature"] = _normalize_temperature_value(option_groups.get("temperature"))
        store_id = meta.get("store_id")
        kiosk_type = meta.get("kiosk_type")

        if not store_id or not kiosk_type or not item_name:
            action = {
                "reply": {
                    "action_type": "answer",
                    "text": "메뉴 정보를 확인하지 못했어요. 다시 한 번 말씀해 주세요.",
                    "ui_hints": {"domain": domain, "intent": intent},
                    "message_key_ok": message_key_ok,
                    "message_key_fail": message_key_fail,
                }
            }
            new_state = _merge_state(state, {"debug_last_reason": "missing_meta_or_item_name"})
            return action, new_state

        if catalog is None:
            catalog = default_catalog_repo()

        item = catalog.get_item_by_name(store_id=store_id, kiosk_type=kiosk_type, name=item_name)
        used_name = item_name
        recovered = False
        if not item:
            cands = _recover_item_name_candidates(item_name, option_groups)
            for cand in cands:
                it2 = catalog.get_item_by_name(store_id=store_id, kiosk_type=kiosk_type, name=cand)
                if it2:
                    item = it2
                    used_name = cand
                    recovered = True
                    break
            if log_event and trace_id:
                log_event(trace_id, "validator_item_lookup_retry", {"original": item_name, "candidates": cands, "recovered": recovered})

        if not item:
            action = {
                "reply": {
                    "action_type": "answer",
                    "text": f"'{item_name}' 메뉴를 찾지 못했어요. 다른 메뉴를 선택해 주세요.",
                    "ui_hints": {"domain": domain, "intent": intent},
                    "message_key_ok": message_key_ok,
                    "message_key_fail": message_key_fail,
                }
            }
            new_state = _merge_state(state, {"debug_last_reason": "menu_not_found"})
            return action, new_state

        slots_for_policy = dict(slots or {})
        if recovered:
            slots_for_policy["item_name"] = {"value": used_name, "confidence": 0.6}

        required_groups = get_required_option_groups_for_add_item(req={"meta": meta}, slots=slots_for_policy, catalog=catalog)
        missing_group = find_missing_required_option_group(required_groups=required_groups, option_groups_slot=option_groups)

        if missing_group:
            prompt_map = {"temperature": "뜨거운/아이스 중 어떤 걸로 드릴까요?", "size": "사이즈는 어떤 걸로 드릴까요? (S/M/L)"}
            text = prompt_map.get(missing_group, f"{missing_group} 옵션을 선택해 주세요.")
            choices = None
            if item.option_groups:
                choices = item.option_groups.get(missing_group)
            action = {
                "reply": {
                    "action_type": "ask_option_group",
                    "text": text,
                    "ui_hints": {"domain": domain, "intent": intent, "expect_option_group": missing_group, "choices": choices},
                }
            }
            slots_min = {
                "item_name": slots.get("item_name"),
                "quantity": slots.get("quantity"),
                "option_groups": {"value": dict(option_groups), "confidence": 0.9},
                "notes": slots.get("notes"),
            }
            new_state = _merge_state(
                state,
                {
                    "current_domain": domain,
                    "active_intent": intent,
                    "slots": slots_min,
                    "pending_option_group": missing_group,
                    "pending_option_group_choices": choices,
                    "last_bot_action": "ask_option_group",
                    "debug_last_reason": f"missing_option_group:{missing_group}",
                },
            )
            return action, new_state

        action = {
            "reply": {
                "action_type": "add_to_cart",
                "text": f"{item.name} {quantity}개를 장바구니에 담았어요.",
                "ui_hints": {"domain": domain, "intent": intent},
                "payload": {"item_id": item.item_id, "name": item.name, "price": item.price, "quantity": quantity, "option_groups": option_groups},
            }
        }
        new_state = _merge_state(
            state,
            {
                "current_domain": domain,
                "active_intent": None,
                "slots": {},
                "last_bot_action": "add_to_cart",
                "debug_last_reason": "added_to_cart",
                "pending_option_group": None,
                "pending_option_group_choices": None,
            },
        )
        return action, new_state

    # [Education Domain]
    if domain == "education":
        new_state = _merge_state(
            state,
            {
                "current_domain": "education",
                "active_intent": intent,
                "slots": slots or {},
                "last_bot_action": "answer",
                "debug_last_reason": f"edu:llm_generate:{intent}",
            },
        )
        llm_task = _edu_make_llm_task(intent=intent, slots=slots, meta=meta, state=new_state)
        action = {
            "reply": {
                "text": "처리할게요.",
                "action_type": "answer",
                "ui_hints": {"domain": domain, "intent": intent},
                "message_key_ok": message_key_ok,
                "message_key_fail": message_key_fail,
            },
            "llm_task": llm_task,
        }
        return action, new_state

    # [Fallback]
    action = {
        "reply": {
            "text": TEMPLATES.get(message_key_ok, "") or "처리할게요.",
            "action_type": "answer",
            "ui_hints": {"domain": domain, "intent": intent},
            "message_key_ok": message_key_ok,
            "message_key_fail": message_key_fail,
        }
    }
    new_state = _merge_state(state, {"debug_last_reason": "action:planned"})
    return action, new_state