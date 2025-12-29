# rag/site_nav_retriever.py
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from rag.site_nav_index import ensure_index_fresh, DEFAULT_DB_PATH, DEFAULT_BASE_URL, DEFAULT_SITEMAP_PATH


@dataclass(frozen=True)
class SearchHit:
    menu_name: str
    breadcrumb: str
    url: str
    section: str


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _sanitize_query(q: str) -> str:
    # FTS5 query에서 따옴표/특수문자 때문에 에러나는 것 최소 방지
    q = (q or "").strip()
    q = q.replace('"', " ")
    q = q.replace("'", " ")
    q = " ".join(q.split())
    return q


def _to_fts_query(q: str) -> str:
    """
    한국어는 토큰화가 제한적이라도,
    prefix 검색(*)을 섞으면 '학습 단어' -> '학습* AND 단어*' 식으로 어느 정도 커버됨.
    """
    q = _sanitize_query(q)
    if not q:
        return ""
    terms = [t for t in q.split() if t]
    # 너무 길면 자름
    terms = terms[:6]
    return " AND ".join([f'{t}*' for t in terms])


def search_site_nav(
    *,
    query: str,
    topk: int = 3,
    db_path: str = DEFAULT_DB_PATH,
    base_url: str = DEFAULT_BASE_URL,
    sitemap_path: str = DEFAULT_SITEMAP_PATH,
) -> List[SearchHit]:
    """
    Returns top matches from local sitemap navigation index.
    Will refresh index if stale (TTL controlled by env SITE_NAV_TTL_SECONDS).
    """
    # 자동 갱신(없거나 오래되면 sitemap 재수집)
    ensure_index_fresh(base_url=base_url, sitemap_path=sitemap_path, db_path=db_path)

    fts_q = _to_fts_query(query)
    if not fts_q:
        return []

    con = _connect(db_path)
    try:
        cur = con.execute(
            """
            SELECT e.menu_name, e.breadcrumb, e.url, e.section
            FROM nav_fts f
            JOIN nav_entries e ON e.id = f.rowid
            WHERE nav_fts MATCH ?
            LIMIT ?
            """,
            (fts_q, int(topk)),
        )
        rows = cur.fetchall()
        hits = [
            SearchHit(
                menu_name=str(r["menu_name"]),
                breadcrumb=str(r["breadcrumb"]),
                url=str(r["url"]),
                section=str(r["section"]),
            )
            for r in rows
        ]
        if hits:
            return hits

        # FTS가 안 잡히는 경우를 위한 fallback: LIKE
        q2 = _sanitize_query(query)
        cur2 = con.execute(
            """
            SELECT menu_name, breadcrumb, url, section
            FROM nav_entries
            WHERE menu_name LIKE ? OR breadcrumb LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (f"%{q2}%", f"%{q2}%", int(topk)),
        )
        rows2 = cur2.fetchall()
        return [
            SearchHit(
                menu_name=str(r["menu_name"]),
                breadcrumb=str(r["breadcrumb"]),
                url=str(r["url"]),
                section=str(r["section"]),
            )
            for r in rows2
        ]
    finally:
        con.close()
