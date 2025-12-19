# nlu/normalizer.py
from __future__ import annotations
from typing import Dict, Any, Optional

def _parse_temperature(text: str) -> Optional[str]:
    t = text.lower()
    if "아이스" in text or "ice" in t:
        return "ice"
    if "뜨거" in text or "따뜻" in text or "hot" in t:
        return "hot"
    return None

def _parse_size(text: str) -> Optional[str]:
    t = text.lower()
    for s in ["tall", "grande", "venti"]:
        if s in t:
            return s
    return None

def apply_session_rules(state: Dict[str, Any], nlu: Dict[str, Any], user_message: str) -> Dict[str, Any]:
    """
    핵심: 직전 봇이 ask_option_group:<group> 상태면,
    - 현재 NLU가 fallback이어도 intent/domain을 유지하고
    - state에 저장된 item_name/quantity/option_groups를 기반으로
      option_groups만 업데이트해서 validator로 넘긴다.
    """
    last = (state.get("last_bot_action") or "")
    if not last.startswith("ask_option_group:"):
        return nlu

    group = last.split(":", 1)[1].strip()
    msg = user_message or ""

    # 1) intent/domain 강제 유지
    nlu = dict(nlu or {})
    nlu["domain"] = state.get("current_domain") or nlu.get("domain") or "kiosk"
    nlu["intent"] = state.get("active_intent") or nlu.get("intent") or "add_item"

    # 2) state에 있던 주문 핵심 슬롯 복원
    st_slots = state.get("slots") or {}
    item_name = st_slots.get("item_name")
    quantity = st_slots.get("quantity") or 1
    opt_map = st_slots.get("option_groups") or {}   # dict 형태로 저장되어 있음

    # 3) 이번 턴 메시지에서 해당 option group value 파싱
    value = None
    if group == "temperature":
        value = _parse_temperature(msg)
    elif group == "size":
        value = _parse_size(msg)
    else:
        # 다른 그룹들은 일단 raw로 저장(확장 지점)
        value = msg.strip() if msg.strip() else None

    if value is not None:
        opt_map[group] = str(value)

    # 4) validator가 이해할 수 있게 slots 재구성
    # validator는 option_groups를 {"value":[{group,value}]} 형태로도 처리 가능하게 해놨음
    og_list = [{"group": k, "value": v} for k, v in opt_map.items()]

    nlu["slots"] = {
        "item_name": {"value": item_name, "confidence": 0.99 if item_name else 0.2},
        "quantity": {"value": quantity, "confidence": 0.99},
        "option_groups": {"value": og_list, "confidence": 0.9 if og_list else 0.2},
    }

    return nlu
