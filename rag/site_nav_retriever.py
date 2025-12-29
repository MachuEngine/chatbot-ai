# rag/site_nav_retriever.py
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Tuple

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
    q = (q or "").strip()
    q = q.replace('"', " ").replace("'", " ")
    return " ".join(q.split())


def _to_fts_query(q: str) -> str:
    q = _sanitize_query(q)
    if not q:
        return ""
    terms = [t for t in q.split() if len(t) > 1]  # 1글자 제외
    if not terms:
        return ""
    # Prefix search
    return " AND ".join([f'{t}*' for t in terms[:6]])


def _rows_to_hits(rows: List[Any], con: sqlite3.Connection = None) -> List[SearchHit]:
    """
    FTS 결과 또는 일반 SELECT 결과를 SearchHit 객체 리스트로 변환.
    FTS 테이블인 경우 원본 테이블 조회가 필요할 수 있음.
    """
    hits = []
    for r in rows:
        # FTS 테이블(nav_fts)은 rowid를 가지므로, 필요시 원본(nav_entries)을 조회해야 함
        # 여기서는 쿼리 단계에서 이미 조인했거나, 필요한 컬럼을 가져왔다고 가정하고 처리
        
        # 만약 FTS 쿼리 결과에 url 정보가 없다면 id로 다시 조회 (안전 장치)
        if "url" not in r.keys() and con:
            rid = r["rowid"] if "rowid" in r.keys() else r["id"]
            cur = con.execute("SELECT menu_name, breadcrumb, url, section FROM nav_entries WHERE id = ?", (rid,))
            orig = cur.fetchone()
            if orig:
                hits.append(SearchHit(
                    menu_name=str(orig["menu_name"]),
                    breadcrumb=str(orig["breadcrumb"]),
                    url=str(orig["url"]),
                    section=str(orig["section"]),
                ))
            continue

        hits.append(SearchHit(
            menu_name=str(r["menu_name"]),
            breadcrumb=str(r["breadcrumb"]),
            url=str(r["url"]),
            section=str(r["section"]),
        ))
    return hits


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
    Prioritizes Exact/Prefix FTS match, then falls back to Token-based Broad Match.
    """
    ensure_index_fresh(base_url=base_url, sitemap_path=sitemap_path, db_path=db_path)

    query = _sanitize_query(query)
    if not query:
        return []

    con = _connect(db_path)
    try:
        # ---------------------------------------------------------
        # Strategy 1: FTS Exact/Prefix Match (가장 정확하고 빠름)
        # ---------------------------------------------------------
        fts_q = _to_fts_query(query)
        if fts_q:
            # nav_fts와 nav_entries를 조인하여 바로 데이터 가져옴
            cur = con.execute(
                """
                SELECT e.menu_name, e.breadcrumb, e.url, e.section
                FROM nav_fts f
                JOIN nav_entries e ON e.id = f.rowid
                WHERE nav_fts MATCH ?
                LIMIT ?
                """,
                (fts_q, topk),
            )
            rows = cur.fetchall()
            if rows:
                return _rows_to_hits(rows)

        # ---------------------------------------------------------
        # Strategy 2: Token-based Broad Match & Python Scoring (유연함)
        # "문장 학습 링크" -> "문장" OR "학습" OR "링크" 중 많이 포함된 순
        # ---------------------------------------------------------
        tokens = [t for t in query.split() if len(t) >= 2]  # 2글자 이상 토큰만
        if not tokens:
            return []

        # SQL Query 생성: menu_name LIKE %t1% OR menu_name LIKE %t2% ...
        clauses = []
        params = []
        for t in tokens:
            clauses.append("menu_name LIKE ?")
            params.append(f"%{t}%")
        
        where_clause = " OR ".join(clauses)
        
        # SQL에서는 후보군을 넉넉히(50개) 가져와서 Python에서 점수 계산
        cur2 = con.execute(
            f"SELECT id, menu_name, breadcrumb, url, section FROM nav_entries WHERE {where_clause} LIMIT 50",
            tuple(params)
        )
        candidates = cur2.fetchall()
        
        # Scoring Logic
        scored_hits = []
        for r in candidates:
            score = 0
            # 검색 대상 텍스트 (메뉴명 + 경로)
            txt = (str(r["menu_name"]) + " " + str(r["breadcrumb"])).lower()
            
            # 토큰 매칭 개수만큼 점수 증가
            for t in tokens:
                if t.lower() in txt:
                    score += 1
            
            # 보정: 검색어가 메뉴명에 공백 없이 포함되면 가산점 (예: "문장학습" -> "문장 학습")
            q_compact = query.replace(" ", "").lower()
            r_compact = str(r["menu_name"]).replace(" ", "").lower()
            if q_compact in r_compact:
                score += 3
                
            scored_hits.append((score, r))
            
        # 점수 높은 순 정렬
        scored_hits.sort(key=lambda x: x[0], reverse=True)
        
        # Top K 추출
        final_rows = [x[1] for x in scored_hits[:topk]]
        
        return _rows_to_hits(final_rows)

    finally:
        con.close()