import random
import re
import sqlite3
import time
import urllib.parse
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests

from scrape_locations import (
    allowed,
    article_links,
    categories_in,
    decode_response,
    extract_js_info,
    maps_url,
    soup_of,
    split_fumi_places,
    visible_lines,
    work_summary,
)

DB_PATH = "locations_master.db"
USER_AGENT = "location-ai-crawler/1.0 (personal location research)"
LOCA_HOSTS = {"loca.ash.jp", "www.loca.ash.jp"}
FUMI_HOST = "fumichen2.livedoor.blog"
SKIP_PREFIXES = ("/photo/", "/photot/", "/cast/", "/web/", "/js/", "/css/", "/bbs/", "/image/")

LOCA_SEEDS = [
    "https://loca.ash.jp/",
    "https://loca.ash.jp/indexyear.htm",
    "https://loca.ash.jp/indexname.htm",
    "https://loca.ash.jp/indexcast.htm",
    "https://loca.ash.jp/indexaddr.htm",
    "https://loca.ash.jp/indexnew.htm",
    "https://loca.ash.jp/indexsts.htm",
    "https://loca.ash.jp/htm/",
]

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})
request_count = 0


def now():
    return datetime.now().isoformat(timespec="seconds")


def pause():
    global request_count
    if request_count:
        time.sleep(random.uniform(1.5, 2.5))
    request_count += 1


def fetch(url):
    if not allowed(url):
        raise RuntimeError(f"robots.txt で取得しないURLです: {url}")
    last_error = None
    for attempt in range(3):
        pause()
        try:
            response = session.get(url, timeout=60)
            response.raise_for_status()
            return decode_response(response)
        except requests.RequestException as error:
            last_error = error
            print(f"  取得失敗 ({attempt + 1}/3): {url} {error}", flush=True)
    raise last_error


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT NOT NULL,
            location_name TEXT,
            address TEXT,
            scenes_category TEXT,
            works_title TEXT,
            detail_text TEXT,
            map_url TEXT,
            source_url TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (source_url, location_name, address, scenes_category)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crawled_pages (
            url TEXT PRIMARY KEY,
            site_name TEXT NOT NULL,
            title TEXT,
            status TEXT NOT NULL,
            record_count INTEGER NOT NULL DEFAULT 0,
            crawled_at TEXT NOT NULL,
            error TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crawl_queue (
            url TEXT PRIMARY KEY,
            site_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            priority INTEGER NOT NULL,
            discovered_at TEXT NOT NULL
        )
    """)
    conn.execute("DELETE FROM crawled_pages WHERE status = 'error'")
    conn.commit()
    return conn


def enqueue(conn, url, site_name, kind, priority):
    if not url or not allowed(url):
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO crawl_queue (url, site_name, kind, priority, discovered_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (url, site_name, kind, priority, now()),
    )


def canonicalize(url, host):
    parsed = urlparse(url)
    path = parsed.path or "/"
    if host == "loca":
        return urllib.parse.urlunparse(("https", "loca.ash.jp", path, "", "", ""))
    query_items = urllib.parse.parse_qs(parsed.query)
    query = f"p={query_items['p'][0]}" if "p" in query_items else ""
    return urllib.parse.urlunparse(("https", FUMI_HOST, path, "", query, ""))


def map_link(lat, lng, address, name):
    link = maps_url(lat, lng, address)
    if link:
        return link
    if name:
        return maps_url(None, None, name)
    return ""


def stored_count(conn):
    return conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]


def mark_page(conn, url, site_name, title, status, record_count, error=""):
    conn.execute(
        """
        INSERT INTO crawled_pages (url, site_name, title, status, record_count, crawled_at, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title = excluded.title,
            status = excluded.status,
            record_count = excluded.record_count,
            crawled_at = excluded.crawled_at,
            error = excluded.error
        """,
        (url, site_name, title, status, record_count, now(), error),
    )


def save_rows(conn, rows):
    inserted = 0
    for row in rows:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO locations (
                site_name, location_name, address, scenes_category, works_title,
                detail_text, map_url, source_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["site_name"],
                row["location_name"],
                row["address"],
                row["scenes_category"],
                row["works_title"],
                row["detail_text"],
                row["map_url"],
                row["source_url"],
                now(),
            ),
        )
        inserted += cursor.rowcount
    return inserted


def next_task(conn):
    return conn.execute(
        """
        SELECT q.url, q.site_name, q.kind
        FROM crawl_queue q
        LEFT JOIN crawled_pages c ON c.url = q.url
        WHERE c.url IS NULL
        ORDER BY q.priority, q.rowid
        LIMIT 1
        """
    ).fetchone()


def progress(conn, site_name, title):
    done = conn.execute(
        "SELECT COUNT(*) FROM crawled_pages WHERE site_name = ? AND status = 'ok'",
        (site_name,),
    ).fetchone()[0]
    queued = conn.execute(
        "SELECT COUNT(*) FROM crawl_queue WHERE site_name = ?",
        (site_name,),
    ).fetchone()[0]
    print(
        f"[{site_name}] {done}/{queued} {title} / 蓄積完了 {stored_count(conn)} 件",
        flush=True,
    )


def consider_loca_link(conn, raw_url, page_url):
    absolute = urljoin(page_url, raw_url).split("#")[0]
    parsed = urlparse(absolute)
    if parsed.netloc not in LOCA_HOSTS:
        return
    url = canonicalize(absolute, "loca")
    path = urlparse(url).path or "/"
    if not allowed(url) or any(path.startswith(prefix) for prefix in SKIP_PREFIXES):
        return
    if path.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".zip", ".pdf")):
        return
    if path.startswith("/info/") and path.endswith(".htm"):
        enqueue(conn, url, "全国ロケ地ガイド", "work", 1)
    elif path.endswith((".htm", ".html")) or path in {"/", "/htm", "/htm/"} or path.startswith("/htm/") or path.startswith("/show/"):
        enqueue(conn, url, "全国ロケ地ガイド", "list", 0)


def loca_rows(page, url):
    page_soup = soup_of(page)
    title = ""
    heading = page_soup.find("h1")
    if heading:
        title = heading.get_text("", strip=True)
    elif page_soup.title:
        title = page_soup.title.get_text("", strip=True)
    work = work_summary(page_soup) or title
    rows = []
    for item in extract_js_info(page):
        scene = " / ".join(
            bit for bit in (item["scene"], categories_in(item["name"], item["scene"], item["address"])) if bit
        )
        detail = " / ".join(bit for bit in (item["scene"], item["address"], item["note"]) if bit)
        rows.append({
            "site_name": "全国ロケ地ガイド",
            "location_name": item["name"],
            "address": item["address"],
            "scenes_category": scene,
            "works_title": work,
            "detail_text": detail,
            "map_url": map_link(item["lat"], item["lng"], item["address"], item["name"]),
            "source_url": url,
        })
    return title, rows


def seed_loca(conn):
    for url in LOCA_SEEDS:
        enqueue(conn, url, "全国ロケ地ガイド", "list", 0)
    conn.commit()


def fumi_page_numbers(page_soup):
    numbers = []
    for anchor in page_soup.find_all("a", href=True):
        query = urllib.parse.parse_qs(urlparse(urljoin("https://fumichen2.livedoor.blog/", anchor["href"])).query)
        if "p" in query and query["p"][0].isdigit():
            numbers.append(int(query["p"][0]))
    return numbers


def fumi_rows(page, url):
    page_soup = soup_of(page)
    title_node = page_soup.select_one(".article-title")
    title = title_node.get_text(" ", strip=True) if title_node else (page_soup.title.get_text(strip=True) if page_soup.title else url)
    date_node = page_soup.select_one(".article-date")
    posted = date_node.get_text(" ", strip=True) if date_node else ""
    categories = "、".join(anchor.get_text(strip=True) for anchor in page_soup.select(".article-category a"))
    article = page_soup.select_one("div.article-body")
    lines = visible_lines(article) if article else []
    detail = "\n".join(lines)
    work = " ".join(part for part in (title, posted, categories) if part)
    rows = []
    for name, address, _block in split_fumi_places(lines, title):
        scene = " / ".join(bit for bit in (categories_in(name, detail, title), categories) if bit)
        rows.append({
            "site_name": "fumi Diary 2号店",
            "location_name": name,
            "address": address,
            "scenes_category": scene,
            "works_title": work,
            "detail_text": detail,
            "map_url": map_link(None, None, address, name),
            "source_url": url,
        })
    return title, rows


def seed_fumi(conn):
    enqueue(conn, "https://fumichen2.livedoor.blog/", "fumi Diary 2号店", "list", 2)
    conn.commit()


def expand_fumi_list(conn, page, page_url):
    page_soup = soup_of(page)
    numbers = fumi_page_numbers(page_soup)
    if numbers and page_url.rstrip("/") == "https://fumichen2.livedoor.blog":
        for number in range(1, max(numbers) + 1):
            enqueue(conn, f"https://fumichen2.livedoor.blog/?p={number}", "fumi Diary 2号店", "list", 2)
    for anchor in page_soup.find_all("a", href=True):
        absolute = urljoin(page_url, anchor["href"]).split("#")[0]
        parsed = urlparse(absolute)
        if parsed.netloc != FUMI_HOST:
            continue
        path = parsed.path or "/"
        if path.startswith("/archives/cat_") or re_month(path):
            enqueue(conn, canonicalize(absolute, "fumi"), "fumi Diary 2号店", "list", 2)
    for article_url in fumi_title_links(page_soup, page_url):
        enqueue(conn, article_url, "fumi Diary 2号店", "article", 3)


def re_month(path):
    return re.search(r"/archives/\d{4}(?:-\d{2})?\.html$", path) is not None


def fumi_title_links(page_soup, page_url):
    links = []
    nodes = page_soup.select(".article-title a[href], .entry-title a[href]")
    for node in nodes:
        absolute = canonicalize(urljoin(page_url, node["href"]).split("#")[0], "fumi")
        path = urlparse(absolute).path
        if "/archives/" in path and path.endswith(".html") and "/cat_" not in path and absolute not in links:
            links.append(absolute)
    if links:
        return links
    return [canonicalize(link, "fumi") for link in article_links(page_soup.prettify(), page_url)]


def handle_loca(conn, url, kind):
    page = fetch(url)
    page_soup = soup_of(page)
    for anchor in page_soup.find_all("a", href=True):
        consider_loca_link(conn, anchor["href"], url)
    title, rows = ("", [])
    if kind == "work" or "jsInfo[" in page:
        title, rows = loca_rows(page, url)
    elif page_soup.find("h1"):
        title = page_soup.find("h1").get_text("", strip=True)
    elif page_soup.title:
        title = page_soup.title.get_text("", strip=True)
    inserted = save_rows(conn, rows)
    mark_page(conn, url, "全国ロケ地ガイド", title, "ok", inserted)
    conn.commit()
    progress(conn, "全国ロケ地ガイド", title or url)


def handle_fumi(conn, url, kind):
    page = fetch(url)
    if kind == "list":
        expand_fumi_list(conn, page, url)
        title = f"一覧 {url}"
        rows = []
    else:
        title, rows = fumi_rows(page, url)
    inserted = save_rows(conn, rows)
    mark_page(conn, url, "fumi Diary 2号店", title, "ok", inserted)
    conn.commit()
    progress(conn, "fumi Diary 2号店", title or url)


def crawl():
    conn = connect()
    seed_loca(conn)
    print("全件収集を開始します。中断した場合は同じコマンドで続きから再開できます。", flush=True)
    try:
        while True:
            task = next_task(conn)
            if task is None:
                if not conn.execute(
                    "SELECT 1 FROM crawl_queue WHERE site_name = ? LIMIT 1",
                    ("fumi Diary 2号店",),
                ).fetchone():
                    print("全国ロケ地ガイドの巡回が終わったので、fumi Diary 2号店を開始します。", flush=True)
                    seed_fumi(conn)
                    continue
                break
            url, site_name, kind = task
            try:
                if site_name == "全国ロケ地ガイド":
                    handle_loca(conn, url, kind)
                else:
                    handle_fumi(conn, url, kind)
            except Exception as error:
                mark_page(conn, url, site_name, "", "error", 0, str(error))
                conn.commit()
                print(f"  スキップ: {url} ({error})", flush=True)
    except KeyboardInterrupt:
        conn.commit()
        print(f"\n中断しました。蓄積 {stored_count(conn)} 件。再実行で続きから再開できます。", flush=True)
        return
    print(f"完了。蓄積 {stored_count(conn)} 件。", flush=True)


if __name__ == "__main__":
    crawl()
