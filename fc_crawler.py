"""1都7県のフィルムコミッションと県の観光・プロモーション窓口から、公開ページのロケ地を集める。

robots.txt を見てから取得し、連続アクセスは間隔を空ける。
取得した施設名、住所、シーン、ロケ弁の記載は locations と auto_learned_locations に追記し、
同じ出典URLの既存行は更新する。

例:
  python3 fc_crawler.py --limit 20
  python3 fc_crawler.py --prefecture 東京都 --limit 40
  python3 fc_crawler.py --refresh --limit 10
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import urllib.robotparser
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "locations_master.db"
BENTO_NOTES_PATH = BASE / "fc_bento_notes.json"
JUNK_NAME_RE = re.compile(
    r"募集|フォーム|お問い合わせ|とは|について|皆さま|登録しませんか|Q&A|Ｑ＆Ａ|トピックス|最新情報|エキストラ|お知らせ|一覧$|検索結果|@"
)
GENERIC_NAMES = {"スポーツ施設", "市内公園", "建物", "観光情報", "ロケ地検索", "撮影実績", "制作会社様へ", "foreign languages"}
USER_AGENT = "YUMI-CHA-fc-crawler/1.0 (location research; contact via almond bagger)"
REQUEST_INTERVAL = 1.5
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

PREFECTURE_NAMES = (
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
)

SOURCES = [
    {
        "prefecture": "東京都",
        "site_name": "東京ロケーションボックス",
        "home": "https://www.locationbox.metro.tokyo.lg.jp/",
        "sitemap": "https://www.locationbox.metro.tokyo.lg.jp/wp-sitemap.xml",
        "include": ("/catalog/",),
    },
    {
        "prefecture": "神奈川県",
        "site_name": "神奈川ロケーションサポートデスク",
        "home": "https://www.pref.kanagawa.jp/docs/b6m/cnt/f80022/p811391.html",
        "include": ("location", "film", "fc", "ロケ"),
    },
    {
        "prefecture": "神奈川県",
        "site_name": "観光かながわNOW",
        "home": "https://kanagawa-kankou.or.jp/features/film-commission",
        "include": ("film", "location", "fc"),
    },
    {
        "prefecture": "千葉県",
        "site_name": "千葉県フィルムコミッション",
        "home": "https://fc.ccb.or.jp/location/",
        "sitemap": "https://fc.ccb.or.jp/sitemap.xml",
        "include": ("/location",),
    },
    {
        "prefecture": "埼玉県",
        "site_name": "埼玉県ロケーションサービス",
        "home": "https://chocotabi-saitama.jp/business/location-top/",
        "include": ("location", "fc", "ロケ"),
    },
    {
        "prefecture": "茨城県",
        "site_name": "いばらきフィルムコミッション",
        "home": "https://www.ibarakiguide.jp/ibaraki-fc/",
        "include": ("ibaraki-fc", "location", "spot", "ロケ"),
    },
    {
        "prefecture": "群馬県",
        "site_name": "ぐんまフィルムコミッション",
        "home": "https://www.gunma-fc.jp/",
        "sitemap": "https://www.gunma-fc.jp/sitemap.xml",
        "include": ("/location", "/spot", "/place", "/works", "/catalog"),
    },
    {
        "prefecture": "栃木県",
        "site_name": "栃木県フィルムコミッション",
        "home": "https://www.tochigi-film.jp/",
        "include": ("location", "spot", "scene", "search", "ロケ"),
    },
    {
        "prefecture": "山梨県",
        "site_name": "富士の国やまなしフィルム・コミッション",
        "home": "https://www.yamanashi-kankou.jp/fc/",
        "include": ("/fc/", "location", "spot", "ロケ"),
    },
    {
        "prefecture": "東京都",
        "site_name": "八王子フィルムコミッション",
        "home": "https://hachioji-fc.com/location/",
        "include": ("/location/",),
        "batch": "recipients",
    },
    {
        "prefecture": "東京都",
        "site_name": "三鷹フィルムコミッション",
        "home": "http://mitaka-fc.jp/",
        "include": ("location", "spot", "ロケ", "弁当"),
        "batch": "recipients",
    },
    {
        "prefecture": "神奈川県",
        "site_name": "かわさきロケ情報",
        "home": "https://www.kawasaki-location.org/location/public.html",
        "include": ("/location/",),
        "batch": "recipients",
    },
    {
        "prefecture": "神奈川県",
        "site_name": "さがみはらフィルムコミッション",
        "home": "https://www.sagamihara-fc.jp/",
        "include": ("location", "spot", "ロケ"),
        "batch": "recipients",
    },
    {
        "prefecture": "神奈川県",
        "site_name": "湘南藤沢フィルム・コミッション",
        "home": "http://www.shonanfujisawa.jp/",
        "include": ("location", "ロケ", "film"),
        "batch": "recipients",
    },
    {
        "prefecture": "神奈川県",
        "site_name": "横須賀市ロケーションサポート",
        "home": "https://yokosuka-kanko.com/location/",
        "include": ("/location",),
        "batch": "recipients",
    },
    {
        "prefecture": "千葉県",
        "site_name": "千葉県フィルムコミッション検索",
        "home": "https://fc.ccb.or.jp/location/",
        "include": ("/location/",),
        "batch": "recipients",
    },
    {
        "prefecture": "千葉県",
        "site_name": "柏市フィルムコミッション",
        "home": "https://www.city.kashiwa.lg.jp/city-promotion/kashiwafc/20230426.html",
        "include": ("kashiwafc", "location", "ロケ"),
        "batch": "recipients",
    },
    {
        "prefecture": "千葉県",
        "site_name": "銚子フィルムコミッション",
        "home": "https://www.city.choshi.chiba.jp/kanko/page110018.html",
        "include": ("kanko", "ロケ"),
        "batch": "recipients",
    },
    {
        "prefecture": "千葉県",
        "site_name": "富津市フィルムコミッション",
        "home": "https://www.city.futtsu.lg.jp/0000006735.html",
        "include": ("000000", "ロケ"),
        "batch": "recipients",
    },
    {
        "prefecture": "千葉県",
        "site_name": "鴨川市フィルムコミッション",
        "home": "https://www.kamogawa-fc.jp/",
        "include": ("location", "spot", "ロケ", "弁当"),
        "batch": "recipients",
    },
    {
        "prefecture": "埼玉県",
        "site_name": "所沢市ロケーションサービス",
        "home": "https://www.city.tokorozawa.saitama.jp/iitokoro/enjoy/location/index.html",
        "include": ("/location/", "弁当"),
        "batch": "recipients",
    },
    {
        "prefecture": "埼玉県",
        "site_name": "川越ロケーションサービス",
        "home": "https://www.city.kawagoe.saitama.jp/kanko/k-other/1011712.html",
        "include": ("kanko", "location", "ロケ"),
        "batch": "recipients",
    },
    {
        "prefecture": "埼玉県",
        "site_name": "行田フィルムコミッション",
        "home": "https://www.gyoda-kankoukyoukai.jp/filmcommission.html",
        "include": ("film", "location", "ロケ"),
        "batch": "recipients",
    },
    {
        "prefecture": "埼玉県",
        "site_name": "深谷フィルムコミッション",
        "home": "https://fukaya-fc.com/",
        "include": ("location", "spot", "ロケ"),
        "batch": "recipients",
    },
    {
        "prefecture": "埼玉県",
        "site_name": "秩父ロケーションサービス",
        "home": "http://www.chichibu-omotenashi.com/",
        "include": ("location", "ロケ", "film"),
        "batch": "recipients",
    },
    {
        "prefecture": "静岡県",
        "site_name": "浜松フィルムコミッション",
        "home": "http://www.hamamatsu-film.com/location.html",
        "include": ("location", "ロケ"),
        "batch": "recipients",
    },
    {
        "prefecture": "静岡県",
        "site_name": "御殿場市フィルムコミッション",
        "home": "https://www.city.gotemba.lg.jp/appeal/appeal-1/53.html",
        "include": ("appeal", "ロケ"),
        "batch": "recipients",
    },
    {
        "prefecture": "静岡県",
        "site_name": "小山町フィルムコミッション",
        "home": "https://oyama-film.or.jp/",
        "include": ("location", "ロケ", "studio"),
        "batch": "recipients",
    },
    {
        "prefecture": "茨城県",
        "site_name": "みとフィルムコミッション",
        "home": "https://www.city.mito.lg.jp/site/filmcommission/",
        "include": ("filmcommission", "location", "ロケ"),
        "batch": "recipients",
    },
    {
        "prefecture": "栃木県",
        "site_name": "あしかがフィルムコミッション",
        "home": "https://www.ashikaga-eizou.jp/",
        "include": ("location", "ロケ", "spot"),
        "batch": "recipients",
    },
    {
        "prefecture": "群馬県",
        "site_name": "前橋フィルムコミッション",
        "home": "https://www.maebashi-cvb.com/fc",
        "include": ("/fc", "location", "ロケ"),
        "batch": "recipients",
    },
]

ADDRESS_RE = re.compile(
    r"(?:〒\s*\d{3}-?\d{4}\s*)?"
    r"((?:北海道|東京都|京都府|大阪府|.{2,3}県).{3,80})"
)
BENTO_RE = re.compile(r"[^。\n]{0,24}(?:ロケ弁|弁当|ケータリング|仕出し|食事)[^。\n]{0,36}")
LABELS = {
    "施設名": "name",
    "名称": "name",
    "ロケ地名": "name",
    "スポット名": "name",
    "住所": "address",
    "所在地": "address",
    "シーン": "scene",
    "撮影シーン": "scene",
    "カテゴリ": "scene",
    "カテゴリー": "scene",
    "ジャンル": "scene",
}


class FcCrawler:
    def __init__(self, limit: int, refresh: bool, pause: float):
        self.limit = limit
        self.refresh = refresh
        self.pause = pause
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.conn = sqlite3.connect(DB_PATH)
        self.last_fetch = 0.0
        self.bento_notes = load_bento_notes()

    def close(self):
        self.conn.close()

    def pause_turn(self):
        wait = self.pause - (time.monotonic() - self.last_fetch)
        if wait > 0:
            time.sleep(wait)
        self.last_fetch = time.monotonic()

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser = self.robots.get(origin)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            robots_url = origin + "/robots.txt"
            try:
                self.pause_turn()
                response = self.session.get(robots_url, timeout=20)
                if response.status_code >= 400:
                    parser.parse([])
                else:
                    parser.parse(response.text.splitlines())
            except requests.RequestException:
                parser.parse([])
            self.robots[origin] = parser
        return parser.can_fetch(USER_AGENT, url)

    def fetch(self, url: str) -> str:
        if not self.allowed(url):
            print(f"skip robots {url}")
            return ""
        self.pause_turn()
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        return response.text

    def sitemap_urls(self, sitemap_url: str, include: tuple[str, ...], seen: set[str] | None = None) -> list[str]:
        seen = seen or set()
        if sitemap_url in seen or not self.allowed(sitemap_url):
            return []
        seen.add(sitemap_url)
        try:
            text = self.fetch(sitemap_url)
        except requests.RequestException as exc:
            print(f"sitemap failed {sitemap_url}: {exc}")
            return []
        if not text:
            return []
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            return []
        tag = root.tag.rsplit("}", 1)[-1]
        found: list[str] = []
        if tag == "sitemapindex":
            for node in root.findall("sm:sitemap/sm:loc", SITEMAP_NS):
                child = (node.text or "").strip()
                if not child:
                    continue
                if include and not any(part.strip("/") in child for part in include):
                    continue
                found.extend(self.sitemap_urls(child, include, seen))
            return found
        for node in root.findall("sm:url/sm:loc", SITEMAP_NS):
            loc = (node.text or "").strip()
            if loc and any(part in loc for part in include):
                found.append(loc)
        return found

    def known_urls(self, site_name: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT source_url FROM locations WHERE site_name = ?",
            (site_name,),
        )
        return {row[0] for row in rows if row[0]}

    def crawl_source(self, source: dict) -> tuple[int, int]:
        site_name = source["site_name"]
        include = tuple(source.get("include") or ())
        print(f"\n[{source['prefecture']}] {site_name}")
        urls: list[str] = []
        if source.get("sitemap"):
            urls.extend(self.sitemap_urls(source["sitemap"], include or ("/",)))
        else:
            urls.append(source["home"])
            urls.extend(self.linked_pages(source["home"], include))
        deduped = []
        seen = set()
        for url in urls:
            if url not in seen:
                seen.add(url)
                deduped.append(url)
        known = self.known_urls(site_name)
        if self.refresh:
            targets = deduped[: self.limit]
        else:
            targets = [url for url in deduped if url not in known][: self.limit]
        inserted = 0
        updated = 0
        for url in targets:
            try:
                html = self.fetch(url)
            except requests.RequestException as exc:
                print(f"fetch failed {url}: {exc}")
                continue
            if not html:
                continue
            self.remember_bento(source, bento_snippets(html))
            record = extract_record(html, url, source)
            if record is None:
                continue
            status = self.save_record(record)
            if status == "insert":
                inserted += 1
            elif status == "update":
                updated += 1
            print(f"  {status}: {record['location_name']} / {record['address'][:40]}")
        self.conn.commit()
        print(f"  新規 {inserted} / 更新 {updated}")
        return inserted, updated

    def remember_bento(self, source: dict, snippets: list[str]):
        for snippet in snippets:
            key = (source["site_name"], snippet)
            if any((note.get("site_name"), note.get("snippet")) == key for note in self.bento_notes):
                continue
            self.bento_notes.append(
                {
                    "site_name": source["site_name"],
                    "prefecture": source["prefecture"],
                    "snippet": snippet,
                }
            )

    def linked_pages(self, page_url: str, include: tuple[str, ...]) -> list[str]:
        try:
            html = self.fetch(page_url)
        except requests.RequestException:
            return []
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        host = urlparse(page_url).netloc
        found = []
        queue = deque(absolute_links(soup, page_url, host, include))
        seen = set(queue)
        while queue and len(found) < self.limit:
            url = queue.popleft()
            if not self.allowed(url):
                continue
            found.append(url)
        return found

    def save_record(self, record: dict) -> str:
        current = self.conn.execute(
            """
            SELECT id FROM locations
            WHERE source_url = ? AND location_name = ?
            LIMIT 1
            """,
            (record["source_url"], record["location_name"]),
        ).fetchone()
        if current:
            self.conn.execute(
                """
                UPDATE locations
                SET address = ?, scenes_category = ?, detail_text = ?, map_url = ?, site_name = ?
                WHERE id = ?
                """,
                (
                    record["address"],
                    record["scenes_category"],
                    record["detail_text"],
                    record["map_url"],
                    record["site_name"],
                    current[0],
                ),
            )
            status = "update"
        else:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO locations (
                    site_name, location_name, address, scenes_category, works_title,
                    detail_text, map_url, source_url, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    record["site_name"],
                    record["location_name"],
                    record["address"],
                    record["scenes_category"],
                    "",
                    record["detail_text"],
                    record["map_url"],
                    record["source_url"],
                ),
            )
            status = "insert"
        learned = self.conn.execute(
            """
            SELECT id FROM auto_learned_locations
            WHERE source_url = ? AND title = ?
            LIMIT 1
            """,
            (record["source_url"], record["location_name"]),
        ).fetchone()
        description = " / ".join(
            part for part in (record["scenes_category"], record["detail_text"]) if part
        )
        if learned:
            self.conn.execute(
                """
                UPDATE auto_learned_locations
                SET address = ?, work_name = ?, description = ?
                WHERE id = ?
                """,
                (record["address"], record["scenes_category"], description, learned[0]),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO auto_learned_locations (title, work_name, address, description, source_url)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record["location_name"],
                    record["scenes_category"],
                    record["address"],
                    description,
                    record["source_url"],
                ),
            )
        return status


def absolute_links(soup: BeautifulSoup, page_url: str, host: str, include: tuple[str, ...]) -> list[str]:
    found = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"].split("#")[0])
        parsed = urlparse(href)
        if parsed.netloc != host or parsed.scheme not in ("http", "https"):
            continue
        if include and not any(part in href for part in include):
            continue
        if href not in found:
            found.append(href)
    return found


def extract_record(html: str, url: str, source: dict) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    host = urlparse(url).netloc
    if "locationbox.metro.tokyo.lg.jp" in host and "/catalog/" in url:
        record = extract_tokyo_catalog(soup, url, source)
    else:
        record = extract_generic(soup, url, source)
    if record is None:
        return None
    if record["location_name"] in (source["site_name"], "ホーム", "トップ"):
        return None
    if not acceptable_facility(record["location_name"], record["address"]):
        return None
    return record


def acceptable_facility(name: str, address: str) -> bool:
    if len(name) < 2 or len(name) > 42 or JUNK_NAME_RE.search(name) or name.casefold() in GENERIC_NAMES:
        return False
    if len(address) > 90 or "。" in address:
        return False
    if re.search(r"には|について|をご|します|ください", address):
        return False
    if re.search(r"\d|丁目|番地", address):
        return True
    return len(address) <= 22 and bool(re.search(r"(市|区|町|村)$", address))


def bento_snippets(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    found = []
    for match in re.finditer(r"[^。\n]{0,40}(?:ロケ弁|仕出し|ケータリング|弁当)[^。\n]{0,70}", page_text(soup)):
        snippet = clean_space(match.group(0))
        if 8 <= len(snippet) <= 180 and snippet not in found and "記載なし" not in snippet:
            found.append(snippet)
        if len(found) >= 3:
            break
    return found


def load_bento_notes() -> list[dict]:
    if not BENTO_NOTES_PATH.exists():
        return []
    try:
        data = json.loads(BENTO_NOTES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def save_bento_notes(notes: list[dict]):
    BENTO_NOTES_PATH.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_tokyo_catalog(soup: BeautifulSoup, url: str, source: dict) -> dict | None:
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    name = title.split("｜")[0].split("|")[0].strip()
    scene = ""
    category = soup.select_one(".categoryName")
    if category:
        scene = category.get_text(" ", strip=True)
    area = soup.select_one(".catalogDetailAreaWrap .area")
    address = labeled_value(soup, "住所") or labeled_value(soup, "所在地")
    if area and address and area.get_text(strip=True) not in address:
        address = area.get_text(strip=True) + address
    address = ensure_prefecture(clean_space(address), source["prefecture"])
    if not name or not address:
        return None
    return pack_record(name, address, scene, page_text(soup), url, source)


def extract_generic(soup: BeautifulSoup, url: str, source: dict) -> dict | None:
    fields = paired_fields(soup)
    name = fields.get("name") or heading_name(soup)
    address = fields.get("address") or first_address(page_text(soup))
    scene = fields.get("scene") or ""
    address = ensure_prefecture(clean_space(address), source["prefecture"])
    if not name or not address:
        return None
    if source["prefecture"] not in address and not address.startswith(source["prefecture"][:2]):
        return None
    return pack_record(name, address, scene, page_text(soup), url, source)


def pack_record(name: str, address: str, scene: str, text: str, url: str, source: dict) -> dict:
    bento = BENTO_RE.search(text)
    detail = clean_space(bento.group(0)) if bento else "ロケ弁受入情報の記載なし"
    return {
        "site_name": source["site_name"],
        "location_name": clean_space(name)[:120],
        "address": address[:200],
        "scenes_category": clean_space(scene)[:120],
        "detail_text": detail[:300],
        "map_url": "https://www.google.com/maps/search/?api=1&query=" + requests.utils.quote(f"{name} {address}"),
        "source_url": url,
    }


def paired_fields(soup: BeautifulSoup) -> dict[str, str]:
    found: dict[str, str] = {}
    for row in soup.find_all(["tr", "dl"]):
        cells = row.find_all(["th", "dt"])
        values = row.find_all(["td", "dd"])
        for label, value in zip(cells, values):
            key = LABELS.get(clean_space(label.get_text(" ", strip=True)))
            if key and key not in found:
                found[key] = value.get_text(" ", strip=True)
    return found


def labeled_value(soup: BeautifulSoup, label: str) -> str:
    for node in soup.find_all(string=re.compile(rf"^{label}$")):
        parent = getattr(node, "parent", None)
        if parent is None or parent.find_parent(["footer", "address"]):
            continue
        classes = " ".join(parent.get("class") or [])
        if "addressArea" in classes or "copyright" in classes:
            continue
        block = parent.parent.get_text(" ", strip=True) if parent.parent else parent.get_text(" ", strip=True)
        block = re.sub(rf"^{label}\s*", "", block)
        block = re.split(r"アクセス|TEL|電話|FAX|メール", block)[0]
        return clean_space(block)
    return ""


def heading_name(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)
    if soup.title:
        return soup.title.get_text(" ", strip=True).split("｜")[0].split("|")[0]
    return ""


def page_text(soup: BeautifulSoup) -> str:
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return main.get_text("\n", strip=True)


def first_address(text: str) -> str:
    match = ADDRESS_RE.search(text)
    return clean_space(match.group(1)) if match else ""


def ensure_prefecture(address: str, prefecture: str) -> str:
    address = clean_space(address)
    address = re.sub(r"〒?\s*123-4567\s*", "", address)
    address = re.sub(r"〒\s*(\d{3})-?(\d{4})\s*", r"〒\1-\2 ", address)
    address = clean_space(address)
    if not address:
        return ""
    if any(name in address for name in PREFECTURE_NAMES):
        return address
    return f"{prefecture}{address}"


def clean_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def main():
    parser = argparse.ArgumentParser(description="1都7県のFC・観光窓口からロケ地を集めて locations_master.db に保存する")
    parser.add_argument("--limit", type=int, default=30, help="各窓口で今回取得するページ数")
    parser.add_argument("--prefecture", default="", help="東京都、神奈川県 のように1県だけ集める")
    parser.add_argument("--refresh", action="store_true", help="取得済みURLも読み直して更新する")
    parser.add_argument("--pause", type=float, default=REQUEST_INTERVAL, help="リクエスト間隔（秒）")
    parser.add_argument("--batch", default="", help="recipients のように、リスト取り込み分だけ集める")
    args = parser.parse_args()
    if args.limit < 1:
        raise SystemExit("--limit は 1 以上にしてください")
    selected = [
        source for source in SOURCES
        if (not args.prefecture or source["prefecture"] == args.prefecture)
        and (not args.batch or source.get("batch") == args.batch)
    ]
    if not selected:
        known = "、".join(dict.fromkeys(source["prefecture"] for source in SOURCES))
        raise SystemExit(f"対象外です。指定できる県: {known}")
    crawler = FcCrawler(limit=args.limit, refresh=args.refresh, pause=args.pause)
    inserted = updated = 0
    try:
        for source in selected:
            added, changed = crawler.crawl_source(source)
            inserted += added
            updated += changed
    finally:
        save_bento_notes(crawler.bento_notes)
        crawler.close()
    print(f"\n完了: 新規 {inserted} / 更新 {updated}")


if __name__ == "__main__":
    main()
