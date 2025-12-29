# rag/site_nav_index.py
from __future__ import annotations

import os
import time
import sqlite3
from dataclasses import dataclass
from typing import List, Tuple, Optional
from urllib.parse import urljoin

import requests

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore


DEFAULT_BASE_URL = os.getenv("MEDIAZEN_BASE_URL", "https://mediazen.ngrok.app").rstrip("/")
DEFAULT_SITEMAP_PATH = os.getenv("MEDIAZEN_SITEMAP_PATH", "/sitemap")
DEFAULT_DB_PATH = os.getenv("SITE_NAV_DB_PATH", "data/site_nav.sqlite3")
DEFAULT_TTL_SECONDS = int(os.getenv("SITE_NAV_TTL_SECONDS", "86400"))  # 24h


@dataclass(frozen=True)
class NavEntry:
    menu_name: str
    breadcrumb: str
    url: str
    section: str


def _ensure_dirs(db_path: str) -> None:
    d = os.path.dirname(db_path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def _connect(db_path: str) -> sqlite3.Connection:
    _ensure_dirs(db_path)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA temp_store=MEMORY;")
    return con


def _init_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS nav_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_name TEXT NOT NULL,
            breadcrumb TEXT NOT NULL,
            url TEXT NOT NULL,
            section TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
        """
    )
    # FTS5: menu_name / breadcrumb 같이 검색
    con.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS nav_fts
        USING fts5(menu_name, breadcrumb, content='nav_entries', content_rowid='id');
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS nav_meta (
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL
        );
        """
    )
    con.commit()


def _meta_get(con: sqlite3.Connection, key: str) -> Optional[str]:
    cur = con.execute("SELECT v FROM nav_meta WHERE k=?", (key,))
    row = cur.fetchone()
    return row[0] if row else None


def _meta_set(con: sqlite3.Connection, key: str, val: str) -> None:
    con.execute("INSERT INTO nav_meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, val))


def _clear_all(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM nav_fts;")
    con.execute("DELETE FROM nav_entries;")
    con.commit()


def _insert_entries(con: sqlite3.Connection, entries: List[NavEntry], now_ts: int) -> None:
    # bulk insert
    con.executemany(
        "INSERT INTO nav_entries(menu_name, breadcrumb, url, section, updated_at) VALUES(?,?,?,?,?)",
        [(e.menu_name, e.breadcrumb, e.url, e.section, now_ts) for e in entries],
    )
    # sync into FTS (content table 방식이므로 rowid 기반으로 rebuild)
    # 간단/확실: rebuild
    con.execute("INSERT INTO nav_fts(nav_fts) VALUES('rebuild');")
    con.commit()


def _fetch_sitemap_html(base_url: str, sitemap_path: str, timeout: int = 15) -> str:
    url = urljoin(base_url + "/", sitemap_path.lstrip("/"))
    r = requests.get(url, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"Failed to fetch sitemap: {r.status_code} {r.text[:200]}")
    return r.text


def _abs_url(base_url: str, href: str) -> str:
    if not href:
        return ""
    return urljoin(base_url + "/", href)


def _extract_entries_from_sitemap_html(base_url: str, html: str) -> List[NavEntry]:
    if BeautifulSoup is None:
        raise RuntimeError("beautifulsoup4 is required. Install: pip install beautifulsoup4")

    soup = BeautifulSoup(html, "html.parser")

    # 전략:
    # - 섹션 헤딩(h1/h2/h3 등)을 section으로 잡고
    # - 그 아래 링크들을 모아 breadcrumb = "{section} > {menu}"
    #
    # 사이트 구조가 바뀌어도 최대한 버티도록:
    # - 모든 링크(a[href])를 훑되,
    # - 가까운 상위의 헤딩 텍스트를 section으로 추정한다.
    headings = soup.find_all(["h1", "h2", "h3", "h4"])
    heading_positions: List[Tuple[int, str]] = []
    all_nodes = list(soup.descendants)

    # descendants 인덱스 기준으로 헤딩 위치 기록
    idx_map = {id(n): i for i, n in enumerate(all_nodes)}
    for h in headings:
        txt = " ".join((h.get_text(" ") or "").split()).strip()
        if not txt:
            continue
        i = idx_map.get(id(h))
        if i is not None:
            heading_positions.append((i, txt))
    heading_positions.sort(key=lambda x: x[0])

    def guess_section(node) -> str:
        i = idx_map.get(id(node))
        if i is None or not heading_positions:
            return "Sitemap"
        # node 이전의 가장 가까운 헤딩
        sec = "Sitemap"
        for pos, title in heading_positions:
            if pos <= i:
                sec = title
            else:
                break
        return sec

    entries: List[NavEntry] = []
    seen: set[Tuple[str, str]] = set()

    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        name = " ".join((a.get_text(" ") or "").split()).strip()
        if not href or not name:
            continue

        # 외부 링크/앵커만 링크 등도 제거(원하면 완화 가능)
        if href.startswith("#"):
            continue

        absu = _abs_url(base_url, href)
        section = guess_section(a)
        breadcrumb = f"{section} > {name}"

        key = (name, absu)
        if key in seen:
            continue
        seen.add(key)

        entries.append(NavEntry(menu_name=name, breadcrumb=breadcrumb, url=absu, section=section))

    return entries


def ensure_index_fresh(
    *,
    base_url: str = DEFAULT_BASE_URL,
    sitemap_path: str = DEFAULT_SITEMAP_PATH,
    db_path: str = DEFAULT_DB_PATH,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    force: bool = False,
) -> int:
    """
    Ensures sitemap-derived navigation index exists and is fresh.
    Returns number of entries in DB after update.
    """
    con = _connect(db_path)
    try:
        _init_schema(con)

        now_ts = int(time.time())
        last_ts_s = _meta_get(con, "last_index_ts")
        last_ts = int(last_ts_s) if (last_ts_s and last_ts_s.isdigit()) else 0

        if (not force) and last_ts and (now_ts - last_ts) < ttl_seconds:
            cur = con.execute("SELECT COUNT(*) FROM nav_entries")
            return int(cur.fetchone()[0])

        html = _fetch_sitemap_html(base_url, sitemap_path)
        entries = _extract_entries_from_sitemap_html(base_url, html)

        _clear_all(con)
        _insert_entries(con, entries, now_ts)

        _meta_set(con, "last_index_ts", str(now_ts))
        _meta_set(con, "base_url", base_url)
        _meta_set(con, "sitemap_path", sitemap_path)
        con.commit()

        return len(entries)
    finally:
        con.close()
