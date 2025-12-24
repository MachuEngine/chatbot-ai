# domain/kiosk/catalog_sqlite.py
from __future__ import annotations

import json
import sqlite3
from typing import Any, List, Optional

from domain.kiosk.catalog_repo import CatalogRepo, MenuItem


def _safe_str(x: Any) -> str:
    return x if isinstance(x, str) else "" if x is None else str(x)


def _json_loads(s: Optional[str]) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


class SQLiteCatalogRepo(CatalogRepo):
    """
    SQLite 기반 메뉴 조회 Repo.
    - 개발/테스트/소형 매장에서는 SQLite도 실무에서 꽤 씀.
    - 나중에 Postgres/MySQL로 바꿔도, 이 Repo만 교체하면 policy/validator/executor는 그대로 유지 가능.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_item_by_name(self, *, store_id: str, kiosk_type: str, name: str) -> Optional[MenuItem]:
        """
        정확 일치(대소문자 무시) 조회.
        - lower(name)=lower(?) 형태는 인덱스를 잘 못 타므로
          name = ? COLLATE NOCASE + (name COLLATE NOCASE) 인덱스로 개선.
        """
        name = _safe_str(name).strip()
        if not name:
            return None

        sql = """
        SELECT
            item_id, store_id, kiosk_type, name, category, price, currency,
            option_groups_json, required_option_groups_json,
            tags_json, dietary, allergens_json, spicy_level, available
        FROM menu_items
        WHERE store_id = ?
          AND kiosk_type = ?
          AND name = ? COLLATE NOCASE
        LIMIT 1
        """

        with self._conn() as conn:
            row = conn.execute(sql, (store_id, kiosk_type, name)).fetchone()
            if not row:
                return None
            return self._row_to_item(row)

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
        """
        검색/추천용 조회.
        - query: 기본을 prefix 검색으로 두면 (name LIKE 'abc%') 인덱스 활용 가능
          (포함검색 %abc% 가 꼭 필요하면 FTS5로 가는 게 정석)
        - ORDER BY를 넣어 LIMIT 결과가 흔들리지 않게 함
        """
        where = ["store_id = ?", "kiosk_type = ?", "available = 1"]
        params: List[Any] = [store_id, kiosk_type]

        if query:
            q = query.strip()
            if q:
                where.append("name LIKE ? COLLATE NOCASE")
                params.append(f"{q}%")  # prefix 검색 (인덱스 활용)

        if category:
            c = category.strip()
            if c:
                where.append("category = ? COLLATE NOCASE")
                params.append(c)

        if budget_max is not None:
            where.append("(price IS NULL OR price <= ?)")
            params.append(int(budget_max))

        if dietary:
            d = dietary.strip()
            if d:
                where.append("dietary = ? COLLATE NOCASE")
                params.append(d)

        if spicy_level:
            s = spicy_level.strip()
            if s:
                where.append("spicy_level = ? COLLATE NOCASE")
                params.append(s)

        sql = f"""
        SELECT
            item_id, store_id, kiosk_type, name, category, price, currency,
            option_groups_json, required_option_groups_json,
            tags_json, dietary, allergens_json, spicy_level, available
        FROM menu_items
        WHERE {" AND ".join(where)}
        ORDER BY name ASC
        LIMIT ?
        """
        params.append(max(1, int(limit)))

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            items = [self._row_to_item(r) for r in rows]

        # temperature는 “추천 힌트”라서 옵션그룹에 존재하면 통과시키는 정도로만 필터
        if temperature:
            t = temperature.strip().lower()
            filtered: List[MenuItem] = []
            for it in items:
                og = it.option_groups or {}
                temps = og.get("temperature")
                if temps is None:
                    filtered.append(it)
                else:
                    if any((_safe_str(x).lower() == t) for x in temps):
                        filtered.append(it)
            items = filtered

        return items

    def _row_to_item(self, row: sqlite3.Row) -> MenuItem:
        option_groups = _json_loads(row["option_groups_json"])
        required_ogs = _json_loads(row["required_option_groups_json"])
        tags = _json_loads(row["tags_json"])
        allergens = _json_loads(row["allergens_json"])

        return MenuItem(
            item_id=_safe_str(row["item_id"]),
            store_id=_safe_str(row["store_id"]),
            kiosk_type=_safe_str(row["kiosk_type"]),
            name=_safe_str(row["name"]),
            category=_safe_str(row["category"]),
            price=row["price"] if row["price"] is not None else None,
            currency=_safe_str(row["currency"]) or "KRW",
            option_groups=option_groups if isinstance(option_groups, dict) else None,
            required_option_groups=required_ogs if isinstance(required_ogs, list) else None,
            tags=tags if isinstance(tags, list) else None,
            dietary=_safe_str(row["dietary"]) or None,
            allergens=allergens if isinstance(allergens, list) else None,
            spicy_level=_safe_str(row["spicy_level"]) or None,
            available=bool(row["available"]),
        )


def init_sqlite_schema(db_path: str) -> None:
    """
    메뉴DB 스키마 생성
    - name exact match(대소문자 무시) 인덱스: (name COLLATE NOCASE)
    - query prefix 검색에 대응: LIKE ... COLLATE NOCASE 가 인덱스를 탈 수 있게 구성
    """
    ddl = """
    CREATE TABLE IF NOT EXISTS menu_items (
        item_id TEXT PRIMARY KEY,
        store_id TEXT NOT NULL,
        kiosk_type TEXT NOT NULL,

        name TEXT NOT NULL,
        category TEXT NOT NULL,
        price INTEGER,
        currency TEXT DEFAULT 'KRW',

        option_groups_json TEXT,
        required_option_groups_json TEXT,

        tags_json TEXT,
        dietary TEXT,
        allergens_json TEXT,
        spicy_level TEXT,

        available INTEGER NOT NULL DEFAULT 1
    );

    CREATE INDEX IF NOT EXISTS idx_menu_items_scope
    ON menu_items(store_id, kiosk_type);

    -- 정확일치/대소문자무시 + prefix 검색 인덱스 활용을 위해 NOCASE 인덱스 사용
    CREATE INDEX IF NOT EXISTS idx_menu_items_name_nocase
    ON menu_items(store_id, kiosk_type, name COLLATE NOCASE);

    -- (선택) 필터가 잦아지면 사용: category/dietary/spicy_level 인덱스
    CREATE INDEX IF NOT EXISTS idx_menu_items_category_nocase
    ON menu_items(store_id, kiosk_type, category COLLATE NOCASE);

    CREATE INDEX IF NOT EXISTS idx_menu_items_dietary_nocase
    ON menu_items(store_id, kiosk_type, dietary COLLATE NOCASE);

    CREATE INDEX IF NOT EXISTS idx_menu_items_spicy_nocase
    ON menu_items(store_id, kiosk_type, spicy_level COLLATE NOCASE);
    """

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(ddl)
        conn.commit()
    finally:
        conn.close()
