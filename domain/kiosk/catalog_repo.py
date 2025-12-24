# domain/kiosk/catalog_repo.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class MenuItem:
    item_id: str
    store_id: str
    kiosk_type: str

    name: str
    category: str
    price: Optional[int] = None
    currency: str = "KRW"

    # {"temperature": ["hot","iced"], "size": ["tall","grande"]} 같은 형태
    option_groups: Optional[Dict[str, List[str]]] = None
    required_option_groups: Optional[List[str]] = None

    tags: Optional[List[str]] = None
    dietary: Optional[str] = None
    allergens: Optional[List[str]] = None
    spicy_level: Optional[str] = None

    available: bool = True


class CatalogRepo:
    """
    메뉴/카탈로그 조회 레이어 인터페이스.
    - 현업에서는 DB(Postgres/MySQL)나 POS/백오피스 API로 대체됨.
    - policy/validator/executor는 이 인터페이스만 알면 된다.
    """

    def get_item_by_name(self, *, store_id: str, kiosk_type: str, name: str) -> Optional[MenuItem]:
        raise NotImplementedError

    def search_items(
        self,
        *,
        store_id: str,
        kiosk_type: str,
        query: Optional[str] = None,
        category: Optional[str] = None,
        budget_max: Optional[int] = None,
        dietary: Optional[str] = None,
        spicy_level: Optional[str] = None,
        temperature: Optional[str] = None,
        limit: int = 12,
    ) -> List[MenuItem]:
        raise NotImplementedError
