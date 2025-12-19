# nlu/validator.py
from __future__ import annotations
from typing import Dict, Any, List
from domain.kiosk.schema import KIOSK_SCHEMA
from domain.kiosk.verticals.loader import load_vertical


def required_slots_for_intent(intent: str) -> List[str]:
    return KIOSK_SCHEMA["intents"].get(intent, {}).get("required_slots", [])


def _option_group_map(option_groups: Any) -> Dict[str, str]:
    """
    option_groups 입력 형태가 아래처럼 섞여 들어와도 안전하게 처리:
      - [{"group":"temperature","value":"ice"}, ...]
      - {"value":[...]}  (slots["option_groups"] 자체가 dict일 때)
      - None
    """
    if option_groups is None:
        return {}

    # {"value":[...]} 형태 처리
    if isinstance(option_groups, dict) and "value" in option_groups:
        option_groups = option_groups.get("value")

    if not isinstance(option_groups, list):
        return {}

    m: Dict[str, str] = {}
    for og in option_groups:
        if not isinstance(og, dict):
            continue
        g = og.get("group")
        v = og.get("value")
        if g and v is not None:
            m[str(g)] = str(v)
    return m


def required_option_groups(vertical: Dict[str, Any], intent: str) -> List[str]:
    policies = (vertical or {}).get("policies", {})
    by_intent = policies.get("required_option_groups_by_intent", {})
    return by_intent.get(intent, [])


def validate_and_build_action(req, state: Dict[str, Any], nlu: Dict[str, Any]):
    domain = nlu.get("domain")
    intent = nlu.get("intent")
    slots: Dict[str, Any] = nlu.get("slots", {}) or {}

    state = {**state}
    state["turn_index"] += 1
    state["current_domain"] = domain
    state["active_intent"] = intent

    if domain != "kiosk":
        return ({"reply": {"text": "현재는 kiosk만 처리합니다.", "action_type": "fallback"}}, state)

    # overlay 로딩(예: cafe 정책)
    vertical = load_vertical(req.meta.kiosk_type)

    # (A) 매장 정보
    if intent == "ask_store_info":
        info_type = (slots.get("info_type", {}) or {}).get("value")
        state["last_bot_action"] = "show_store_info"

        if info_type == "wifi":
            return (
                {"reply": {"text": "와이파이 비밀번호는 카운터에 안내되어 있어요.", "action_type": "show_info"}},
                state
            )

        return (
            {"reply": {"text": f"{info_type} 정보는 직원에게 문의해 주세요.", "action_type": "show_info"}},
            state
        )

    # 1) intent required slots 체크
    required = required_slots_for_intent(intent)
    missing = [s for s in required if not (slots.get(s, {}) or {}).get("value")]

    if missing:
        ask = missing[0]
        state["slots"] = {k: (v or {}).get("value") if isinstance(v, dict) else v for k, v in slots.items()}
        state["last_bot_action"] = f"ask_{ask}"
        return (
            {"reply": {"text": f"{ask} 정보를 알려주세요.", "action_type": "ask_slot", "ui_hints": {"expect_slot": ask}}},
            state
        )

    # (B) add_item 처리
    if intent == "add_item":
        item = (slots.get("item_name", {}) or {}).get("value")
        qty = (slots.get("quantity", {}) or {}).get("value")
        opt_map = _option_group_map(slots.get("option_groups"))

        # overlay 정책: 필수 option_groups 체크
        opt_required = required_option_groups(vertical, intent)
        missing_opts = [g for g in opt_required if g not in opt_map]

        if missing_opts:
            ask_group = missing_opts[0]
            state["slots"] = {
                "item_name": item,
                "quantity": qty,
                "option_groups": opt_map,  # 현재까지 채운 옵션들
            }
            state["last_bot_action"] = f"ask_option_group:{ask_group}"

            # 친화 질문(카페 예시)
            text = f"{ask_group} 옵션을 선택해 주세요."
            if (req.meta.kiosk_type or "").lower() == "cafe" and ask_group == "temperature":
                text = "뜨거운/아이스 중 어떤 걸로 드릴까요?"
            if (req.meta.kiosk_type or "").lower() == "cafe" and ask_group == "size":
                text = "사이즈는 tall / grande / venti 중 어떤 걸로 드릴까요?"

            return (
                {"reply": {"text": text, "action_type": "ask_option_group", "ui_hints": {"expect_option_group": ask_group}}},
                state
            )

        # 성공 처리(장바구니 담기)
        og_list = [{"group": k, "value": v} for k, v in opt_map.items()]

        state["slots"] = {"item_name": item, "quantity": qty, "option_groups": opt_map}
        state["last_bot_action"] = "add_to_cart"

        return (
            {"reply": {
                "text": f"{item} {qty}개를 장바구니에 담았습니다.",
                "action_type": "add_to_cart",
                "payload": {"cart_items": [{"name": item, "qty": qty, "option_groups": og_list}]},
                "ui_hints": {"show_confirm_button": True}
            }},
            state
        )

    return ({"reply": {"text": "아직 해당 기능은 MVP에 없습니다.", "action_type": "fallback"}}, state)
