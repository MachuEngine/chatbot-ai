from __future__ import annotations

from typing import Any, Dict, Optional

from utils.logging import log_event
from domain.kiosk.policy import (
    get_required_option_groups_for_add_item,
    find_missing_required_option_group,
)
from domain.kiosk.catalog_sqlite import SQLiteCatalogRepo

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


def _edu_make_llm_task(
    *,
    intent: str,
    slots: Dict[str, Any],
    meta: Dict[str, Any],
    state: Dict[str, Any],
) -> Dict[str, Any]:
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
        "input": {
            "intent": intent,
            "slots": slots or {},
            "meta": safe_meta,
            "state": safe_state,
        },
        # 참고용(상위 레이어에서 schema 생성하므로 여기선 안내 수준)
        "output_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
                "ui_hints": {"type": "object", "additionalProperties": False},
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
):
    message_key_ok = f"result.{domain}.{intent}"
    message_key_fail = "result.fail.generic"

    # kiosk / add_item (기존 그대로)
    if domain == "kiosk" and intent == "add_item":
        item_name = _slot_value(slots, "item_name")
        quantity = _slot_value(slots, "quantity") or 1
        option_groups = _slot_value(slots, "option_groups") or {}

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

        catalog = SQLiteCatalogRepo(db_path=meta.get("db_path", "data/menu.db"))
        item = catalog.get_item_by_name(
            store_id=store_id,
            kiosk_type=kiosk_type,
            name=item_name,
        )

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

        required_groups = get_required_option_groups_for_add_item(
            req={"meta": meta},
            slots=slots,
            catalog=catalog,
        )

        missing_group = find_missing_required_option_group(
            required_groups=required_groups,
            option_groups_slot=option_groups,
        )

        if missing_group:
            prompt_map = {
                "temperature": "뜨거운/아이스 중 어떤 걸로 드릴까요?",
                "size": "사이즈는 어떤 걸로 드릴까요? (S/M/L)",
            }
            text = prompt_map.get(missing_group, f"{missing_group} 옵션을 선택해 주세요.")

            choices = None
            if item.option_groups:
                choices = item.option_groups.get(missing_group)

            log_event(
                trace_id,
                "validator_missing_option_group",
                {"item": item_name, "missing_group": missing_group, "choices": choices},
            )

            action = {
                "reply": {
                    "action_type": "ask_option_group",
                    "text": text,
                    "ui_hints": {
                        "domain": domain,
                        "intent": intent,
                        "expect_option_group": missing_group,
                        "choices": choices,
                    },
                }
            }

            new_state = _merge_state(
                state,
                {
                    "active_intent": intent,
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
                "payload": {
                    "item_id": item.item_id,
                    "name": item.name,
                    "price": item.price,
                    "quantity": quantity,
                    "option_groups": option_groups,
                },
            }
        }

        new_state = _merge_state(
            state,
            {"last_bot_action": "add_to_cart", "debug_last_reason": "added_to_cart"},
        )
        return action, new_state

    # education: "답변 생성"만 LLM task로 위임
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
                "text": "처리할게요.",  # 상위 레이어에서 llm_task 결과로 교체됨
                "action_type": "answer",
                "ui_hints": {"domain": domain, "intent": intent},
                "message_key_ok": message_key_ok,
                "message_key_fail": message_key_fail,
            },
            "llm_task": llm_task,
        }

        log_event(
            trace_id,
            "validator_edu_llm_task_set",
            {"intent": intent, "slots_keys": list((slots or {}).keys())},
        )

        return action, new_state

    # fallback
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
