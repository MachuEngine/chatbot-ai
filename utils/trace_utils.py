# utils/trace_utils.py
from __future__ import annotations

from typing import Any, Dict, List, Optional


def state_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    state 로그용 요약.
    state["slots"]는 raw dict라는 전제(권장 정책) 기준으로 keys만 노출.
    """
    if not isinstance(state, dict):
        return {"_type": str(type(state))}

    keys = ["conversation_id", "turn_index", "current_domain", "active_intent", "last_bot_action"]
    out: Dict[str, Any] = {k: state.get(k) for k in keys}

    slots = state.get("slots")
    if isinstance(slots, dict):
        out["slots_keys"] = list(slots.keys())

    if "debug_last_reason" in state:
        out["debug_last_reason"] = state.get("debug_last_reason")

    return out


def _unwrap_slot(v: Any) -> Any:
    """
    NLU slots가 slot-dict({value, confidence})일 수도 있고 raw일 수도 있으므로
    비교 시 value 중심으로 unwrap.
    """
    if isinstance(v, dict) and "value" in v:
        return v.get("value")
    return v


def _coerce_slots_dict(x: Any) -> Dict[str, Any]:
    if isinstance(x, dict):
        return x
    return {}


def nlu_diff_hint(before: Dict[str, Any], after: Dict[str, Any], max_changed: int = 30) -> Dict[str, Any]:
    """
    before/after NLU의 변화 요약.
    - domain/intent 변화
    - slots 변경된 키 목록(최대 max_changed개)
    """
    hint: Dict[str, Any] = {}

    for k in ["domain", "intent"]:
        b = before.get(k) if isinstance(before, dict) else None
        a = after.get(k) if isinstance(after, dict) else None
        if b != a:
            hint[k] = {"before": b, "after": a}

    b_slots = _coerce_slots_dict(before.get("slots") if isinstance(before, dict) else None)
    a_slots = _coerce_slots_dict(after.get("slots") if isinstance(after, dict) else None)

    changed: List[str] = []
    for sk in (set(b_slots.keys()) | set(a_slots.keys())):
        bv = _unwrap_slot(b_slots.get(sk))
        av = _unwrap_slot(a_slots.get(sk))
        if bv != av:
            changed.append(sk)

    if changed:
        # 로그 폭주 방지
        changed_sorted = sorted(changed)
        if len(changed_sorted) > max_changed:
            hint["slots_changed"] = changed_sorted[:max_changed]
            hint["slots_changed_truncated"] = True
            hint["slots_changed_count"] = len(changed_sorted)
        else:
            hint["slots_changed"] = changed_sorted

    return hint
