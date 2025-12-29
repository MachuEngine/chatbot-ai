# domain/kiosk/policy.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from domain.kiosk.catalog_repo import CatalogRepo
from domain.kiosk.catalog_sqlite import SQLiteCatalogRepo


# ----------------------------
# helpers
# ----------------------------

def _safe_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _safe_str(x: Any) -> str:
    return x if isinstance(x, str) else "" if x is None else str(x)


def _unwrap_slot_value(v: Any) -> Any:
    if isinstance(v, dict) and "value" in v:
        return v.get("value")
    return v


def _extract_meta(req: Any) -> Dict[str, Any]:
    if isinstance(req, dict):
        return _safe_dict(req.get("meta"))
    return _safe_dict(getattr(req, "meta", None))


def _extract_store_scope(req: Any) -> Tuple[Optional[str], Optional[str]]:
    meta = _extract_meta(req)
    store_id = meta.get("store_id")
    kiosk_type = meta.get("kiosk_type") or meta.get("kioskType")
    store_id = _safe_str(store_id).strip() or None
    kiosk_type = _safe_str(kiosk_type).strip() or None
    return store_id, kiosk_type


# ----------------------------
# Repo factory (default)
# ----------------------------

def default_catalog_repo(db_path: Optional[str] = None) -> CatalogRepo:
    """
    기본 Repo를 SQLite로 둠.
    - db_path: 인자로 받거나, env(KIOSK_MENU_DB_PATH)를 쓰거나, 기본값 data/menu.db 사용
    """
    if not db_path:
        # ✅ [수정] 기본 경로를 ./menu.db -> data/menu.db 로 변경 (Seeder와 일치)
        db_path = os.getenv("KIOSK_MENU_DB_PATH", "data/menu.db")
    
    return SQLiteCatalogRepo(db_path=db_path)


# ----------------------------
# Policy: required option groups for add_item
# ----------------------------

def get_required_option_groups_for_add_item(
    *,
    req: Any,
    slots: Dict[str, Any],
    catalog: Optional[CatalogRepo] = None,
) -> List[str]:
    """
    add_item에서 "필수 option group"을 '메뉴DB 기준'으로 결정한다.
    - 같은 카페 키오스크여도 음료/디저트에 따라 temperature 필수 여부가 다름.
    """
    catalog = catalog or default_catalog_repo()
    store_id, kiosk_type = _extract_store_scope(req)

    # scope 없으면 정책 판단 불가 -> 보수적으로 "없음"
    if not store_id or not kiosk_type:
        return []

    item_name = _safe_str(_unwrap_slot_value(slots.get("item_name"))).strip()
    if not item_name:
        return []

    it = catalog.get_item_by_name(store_id=store_id, kiosk_type=kiosk_type, name=item_name)
    if not it:
        # 못 찾으면 보수적으로 옵션 안 물어봄 (아이템 리졸브 실패를 상위에서 처리)
        return []

    req_ogs = it.required_option_groups or []

    # required_option_groups가 비어있다면 option_groups로부터 유추 가능
    # (temperature option group이 존재하면 필수로 보는 기본 정책)
    if not req_ogs:
        ogs = it.option_groups or {}
        if "temperature" in ogs and isinstance(ogs.get("temperature"), list) and len(ogs.get("temperature")) > 0:
            req_ogs = ["temperature"]

    # 정리(중복 제거)
    out: List[str] = []
    seen = set()
    for g in req_ogs:
        g2 = _safe_str(g).strip()
        if g2 and g2 not in seen:
            seen.add(g2)
            out.append(g2)
    return out


def find_missing_required_option_group(
    *,
    required_groups: Sequence[str],
    option_groups_slot: Any,
) -> Optional[str]:
    """
    required_groups 중 option_groups에 아직 없는 그룹 1개를 반환.
    option_groups_slot:
      - None
      - {"value": {...}, "confidence": ...}
      - dict 자체
    """
    og_val = _unwrap_slot_value(option_groups_slot)
    og_dict = og_val if isinstance(og_val, dict) else {}

    for g in required_groups:
        if not g:
            continue
        v = og_dict.get(g)
        if v is None:
            return g
        if isinstance(v, str) and not v.strip():
            return g
    return None


# ----------------------------
# Policy: build RAG context for recommendation
# ----------------------------

def build_menu_rag_context_for_recommendation(
    *,
    req: Any,
    slots: Dict[str, Any],
    catalog: Optional[CatalogRepo] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """
    ask_recommendation에서 메뉴DB를 조회한 결과를 LLM에 주입할 "RAG 컨텍스트"로 만든다.
    """
    catalog = catalog or default_catalog_repo()
    store_id, kiosk_type = _extract_store_scope(req)

    # scope 없으면 조회 불가 -> 빈 컨텍스트
    if not store_id or not kiosk_type:
        return {
            "store_scope": {"store_id": store_id, "kiosk_type": kiosk_type},
            "menu": [],
            "policy": {"recommend_from_menu_only": True, "if_menu_empty": "apologize_and_ask_filters"},
        }

    category = _safe_str(_unwrap_slot_value(slots.get("category"))).strip() or None
    dietary = _safe_str(_unwrap_slot_value(slots.get("dietary"))).strip() or None
    spicy_level = _safe_str(_unwrap_slot_value(slots.get("spicy_level"))).strip() or None
    temperature = _safe_str(_unwrap_slot_value(slots.get("temperature"))).strip() or None

    budget_max_val = _unwrap_slot_value(slots.get("budget_max"))
    budget_max = int(budget_max_val) if isinstance(budget_max_val, (int, float)) else None

    # schema에 query 슬롯이 없을 수도 있으니 방어
    query = _safe_str(_unwrap_slot_value(slots.get("query"))).strip() or None

    items = catalog.search_items(
        store_id=store_id,
        kiosk_type=kiosk_type,
        query=query,
        category=category,
        budget_max=budget_max,
        dietary=dietary,
        spicy_level=spicy_level,
        temperature=temperature,
        limit=limit,
    )

    menu_cards: List[Dict[str, Any]] = []
    for it in items:
        menu_cards.append({
            "id": it.item_id,
            "name": it.name,
            "category": it.category,
            "price": it.price,
            "currency": it.currency,
            "required_option_groups": it.required_option_groups or [],
            "option_groups": it.option_groups or {},
            "dietary": it.dietary,
            "allergens": it.allergens or [],
            "tags": it.tags or [],
            "available": it.available,
        })

    return {
        "store_scope": {"store_id": store_id, "kiosk_type": kiosk_type},
        "filters": {
            "category": category,
            "budget_max": budget_max,
            "dietary": dietary,
            "spicy_level": spicy_level,
            "temperature": temperature,
            "query": query,
        },
        "menu": menu_cards,
        "policy": {
            "recommend_from_menu_only": True,
            "if_menu_empty": "apologize_and_ask_filters",
        }
    }