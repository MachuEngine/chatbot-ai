# seed_menu_db.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = "data/menu.db"


DDL = """
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

CREATE INDEX IF NOT EXISTS idx_menu_items_name
ON menu_items(store_id, kiosk_type, name);
"""


def j(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def seed_rows():
    # 공통 옵션그룹(예시)
    OG_DRINK = {"temperature": ["hot", "ice"], "size": ["S", "M", "L"]}
    OG_COFFEE = {"temperature": ["hot", "ice"], "size": ["S", "M", "L"], "shot": [0, 1, 2]}
    OG_TEA = {"temperature": ["hot", "ice"], "size": ["S", "M", "L"]}
    OG_DESSERT = {}  # 디저트는 옵션 없음으로 예시

    # 필수 옵션그룹(메뉴별로 다르게 가능)
    REQ_HOTICE = ["temperature"]
    REQ_SIZE = ["size"]
    REQ_HOTICE_SIZE = ["temperature", "size"]

    # store 3개
    stores = ["store_01", "store_02", "store_03"]
    kiosk_type = "cafe"

    # 각 매장에 동일 10개 메뉴(테스트용). 매장별로 가격/available 바꿔도 됨.
    base_menu = [
        # item_id는 store별로 유니크하게
        ("americano", "아메리카노", "coffee", 4500, OG_COFFEE, REQ_HOTICE_SIZE, ["best", "classic"], None, ["caffeine"], None, 1),
        ("cafelatte", "카페라떼", "coffee", 5000, OG_COFFEE, REQ_HOTICE_SIZE, ["milk"], None, ["caffeine", "dairy"], None, 1),
        ("vanillalatte", "바닐라라떼", "coffee", 5500, OG_COFFEE, REQ_HOTICE_SIZE, ["sweet"], None, ["caffeine", "dairy"], None, 1),
        ("caramelmacchiato", "카라멜마키아또", "coffee", 5800, OG_COFFEE, REQ_HOTICE_SIZE, ["sweet"], None, ["caffeine", "dairy"], None, 1),
        ("coldbrew", "콜드브루", "coffee", 5200, {"temperature": ["ice"], "size": ["S", "M", "L"], "shot": [0, 1, 2]}, ["temperature", "size"], ["best"], None, ["caffeine"], None, 1),
        ("greentea", "녹차", "tea", 4800, OG_TEA, REQ_HOTICE_SIZE, ["tea"], "vegan", [], None, 1),
        ("lemonade", "레몬에이드", "ade", 5500, {"temperature": ["ice"], "size": ["S", "M", "L"]}, ["temperature", "size"], ["fresh"], "vegan", [], None, 1),
        ("chocolate", "초코라떼", "beverage", 5300, OG_DRINK, REQ_HOTICE_SIZE, ["sweet"], None, ["dairy"], None, 1),
        ("cheesecake", "치즈케이크", "dessert", 6500, OG_DESSERT, [], ["dessert"], None, ["dairy", "egg", "wheat"], None, 1),
        ("croissant", "크루아상", "bakery", 4200, OG_DESSERT, [], ["bakery"], None, ["wheat", "butter"], None, 1),
    ]

    rows = []
    for store_id in stores:
        for code, name, category, price, og, req, tags, dietary, allergens, spicy, avail in base_menu:
            item_id = f"{store_id}:{kiosk_type}:{code}"
            rows.append(
                (
                    item_id,
                    store_id,
                    kiosk_type,
                    name,
                    category,
                    price,
                    "KRW",
                    j(og) if og else None,
                    j(req) if req else None,
                    j(tags) if tags else None,
                    dietary,
                    j(allergens) if allergens is not None else None,
                    spicy,
                    avail,
                )
            )
    return rows


def main():
    Path("data").mkdir(exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(DDL)

        # 중복 실행해도 안전하게: 같은 item_id면 덮어쓰기 대신 무시
        # (가격/옵션 업데이트까지 하고 싶으면 INSERT OR REPLACE로 바꿔도 됨)
        sql = """
        INSERT OR IGNORE INTO menu_items (
            item_id, store_id, kiosk_type,
            name, category, price, currency,
            option_groups_json, required_option_groups_json,
            tags_json, dietary, allergens_json, spicy_level,
            available
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        rows = seed_rows()
        conn.executemany(sql, rows)
        conn.commit()

        cur = conn.cursor()
        count = cur.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0]
        print(f"[OK] Seed complete. menu_items rows = {count}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
