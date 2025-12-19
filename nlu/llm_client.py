# nlu/llm_client.py
from __future__ import annotations
import re
from typing import Dict, Any, Optional, Tuple

NUM_MAP = {
    "한": 1, "하나": 1, "한잔": 1, "한 잔": 1, "한개": 1, "한 개": 1,
    "두": 2, "둘": 2, "두잔": 2, "두 잔": 2, "두개": 2, "두 개": 2,
    "세": 3, "셋": 3, "세잔": 3, "세 잔": 3, "세개": 3, "세 개": 3,
}

def _extract_quantity(text: str) -> Optional[int]:
    # 1) 숫자 패턴 (예: 1개, 2잔)
    m = re.search(r"(\d+)\s*(개|잔)?", text)
    if m:
        try:
            q = int(m.group(1))
            if 1 <= q <= 20:
                return q
        except ValueError:
            pass

    # 2) 한글 수사
    for k, v in NUM_MAP.items():
        if k in text:
            return v
    return None

def _extract_item_name(text: str) -> Optional[str]:
    # MVP: 카페 예시 품목만 최소로
    # (나중에 store_id catalog_db로 확장하면 여기 제거)
    items = [
        "아메리카노", "라떼", "카페라떼", "카푸치노", "바닐라라떼",
        "콜드브루", "에스프레소",
    ]
    for it in items:
        if it in text:
            return it
    return None

def _extract_option_groups(text: str) -> list[dict]:
    t = text.lower()
    og = []

    # temperature
    if "아이스" in text or "ice" in t:
        og.append({"group": "temperature", "value": "ice"})
    elif "뜨거" in text or "따뜻" in text or "hot" in t:
        og.append({"group": "temperature", "value": "hot"})

    # size
    for s in ["tall", "grande", "venti"]:
        if s in t:
            og.append({"group": "size", "value": s})
            break

    # shots (예: 샷 추가 2 / 샷 1추가)
    m = re.search(r"샷\s*(추가)?\s*(\d+)", text)
    if m:
        og.append({"group": "shots", "value": m.group(2)})

    # takeout
    if "포장" in text or "테이크아웃" in text or "takeout" in t:
        og.append({"group": "takeout", "value": "true"})
    elif "매장" in text or "여기서" in text:
        og.append({"group": "takeout", "value": "false"})

    return og

def nlu_with_llm(req, state: Dict[str, Any], candidates: Dict[str, Any]) -> Dict[str, Any]:
    """
    지금은 더미 규칙 기반.
    - mode=kiosk면 add_item / ask_store_info 정도만 MVP로 처리
    - 나중에 OpenAI JSON 출력으로 교체
    """
    text = req.user_message.strip()
    lower = text.lower()

    # 도메인 고정
    domain = candidates.get("domain", "general")

    # kiosk 아닌 경우 fallback
    if domain != "kiosk":
        return {"domain": domain, "intent": "fallback", "intent_confidence": 0.2, "slots": {}}

    # store info
    if ("와이파이" in text) or ("wifi" in lower):
        return {
            "domain": "kiosk",
            "intent": "ask_store_info",
            "intent_confidence": 0.9,
            "slots": {
                "info_type": {"value": "wifi", "confidence": 0.9}
            }
        }

    # add_item (주문)
    item = _extract_item_name(text)
    qty = _extract_quantity(text) or 1
    og = _extract_option_groups(text)

    # 주문 키워드가 있거나 item을 찾았으면 add_item으로 본다
    if item or ("주세요" in text) or ("주문" in text) or ("할게" in text):
        return {
            "domain": "kiosk",
            "intent": "add_item",
            "intent_confidence": 0.85 if item else 0.6,
            "slots": {
                "item_name": {"value": item, "confidence": 0.9 if item else 0.2},
                "quantity": {"value": qty, "confidence": 0.8},
                "option_groups": {"value": og, "confidence": 0.7 if og else 0.2},
                "notes": {"value": None, "confidence": 0.0},
            }
        }

    return {"domain": "kiosk", "intent": "fallback", "intent_confidence": 0.2, "slots": {}}
