# domain/kiosk/menu_repo.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Dict, Any


@dataclass(frozen=True)
class MenuItem:
    store_id: str
    sku: str
    name: str
    category: str          # "coffee" | "tea" | "dessert" ...
    price: int             # KRW
    tags: List[str]        # ["popular", "sweet", ...]
    requires_temperature: bool
    supports_temperature: bool


class MenuRepository:
    """
    ✅ 현업형 설계 포인트:
    - 여기서 DB(SQL/NoSQL/Cache)를 조회한다.
    - 서비스 로직(policy/추천)은 Repo 인터페이스만 의존.
    """

    def find_item_by_name(self, *, store_id: str, name: str) -> Optional[MenuItem]:
        raise NotImplementedError

    def search_items(
        self,
        *,
        store_id: str,
        category: Optional[str] = None,
        budget_max: Optional[int] = None,
        temperature_hint: Optional[str] = None,  # "hot"|"iced"|None
        limit: int = 8,
    ) -> List[MenuItem]:
        raise NotImplementedError


class InMemoryMenuRepository(MenuRepository):
    """
    ✅ 테스트/개발용 인메모리 Repo.
    현업에선 이 클래스를 DB Repo로 교체하면 됨.
    """

    def __init__(self, items: List[MenuItem]):
        self._items = items

    def _items_for_store(self, store_id: str) -> List[MenuItem]:
        sid = (store_id or "").strip()
        return [it for it in self._items if it.store_id == sid]

    def find_item_by_name(self, *, store_id: str, name: str) -> Optional[MenuItem]:
        if not store_id or not name:
            return None
        n = name.strip()
        items = self._items_for_store(store_id)

        for it in items:
            if it.name == n:
                return it
        for it in items:
            if n and n in it.name:
                return it
        return None

    def search_items(
        self,
        *,
        store_id: str,
        category: Optional[str] = None,
        budget_max: Optional[int] = None,
        temperature_hint: Optional[str] = None,
        limit: int = 8,
    ) -> List[MenuItem]:
        items = self._items_for_store(store_id)

        if category:
            c = category.strip().lower()
            items = [it for it in items if it.category.lower() == c]

        if budget_max is not None:
            items = [it for it in items if it.price <= int(budget_max)]

        if temperature_hint in ("hot", "iced"):
            items = [it for it in items if it.supports_temperature]

        def score(it: MenuItem) -> tuple:
            popular = 0 if "popular" in it.tags else 1
            return (popular, it.price)

        items.sort(key=score)
        return items[: max(1, int(limit))]


def as_rag_cards(items: List[MenuItem]) -> List[Dict[str, Any]]:
    """
    LLM에 넣기 좋은 카드 형태 (문장 생성을 위한 컨텍스트)
    """
    out: List[Dict[str, Any]] = []
    for it in items:
        out.append({
            "name": it.name,
            "category": it.category,
            "price": it.price,
            "tags": it.tags,
            "requires_temperature": it.requires_temperature,
        })
    return out


# ---- 기본 Repo 인스턴스 (개발용) ----
# 현업: 여기 대신 DB Repo를 만들어 get_menu_repo()에서 반환
_DEFAULT_ITEMS: List[MenuItem] = [
    # store A
    MenuItem("store_a", "c_ame", "아메리카노", "coffee", 4500, ["popular", "classic"], True, True),
    MenuItem("store_a", "c_latte", "카페라떼", "coffee", 5500, ["popular", "milk"], True, True),
    MenuItem("store_a", "t_yuzu", "유자차", "tea", 6000, ["sweet"], True, True),
    MenuItem("store_a", "d_cheese", "치즈케이크", "dessert", 6500, ["popular", "sweet"], False, False),

    # store B (지점별로 메뉴/가격 다를 수 있음)
    MenuItem("store_b", "c_ame", "아메리카노", "coffee", 4300, ["popular", "classic"], True, True),
    MenuItem("store_b", "d_choco", "초코케이크", "dessert", 6800, ["sweet"], False, False),
]


_repo_singleton: MenuRepository = InMemoryMenuRepository(_DEFAULT_ITEMS)


def get_menu_repo() -> MenuRepository:
    """
    ✅ DI 포인트:
    - 나중에 DB/캐시 붙이면 여기만 바꾸면 됨.
    """
    return _repo_singleton
