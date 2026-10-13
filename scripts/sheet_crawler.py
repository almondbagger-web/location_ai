"""関東の公開FC・公共撮影施設・ロケ弁情報を、Googleスプレッドシートへ差分追記する。

認証は環境変数 GCP_SA_KEY（サービスアカウントJSON文字列）を優先し、
無いときはプロジェクト直下の google-credentials.json を使う。
個人名、電話番号、台本のシーン番号、制作部の連絡メモは書き込まない。

例:
  python3 scripts/sheet_crawler.py --dry-run --limit 4
  python3 scripts/sheet_crawler.py --limit 8
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

import gspread
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fc_crawler import (  # noqa: E402
    SITEMAP_NS,
    SOURCES,
    USER_AGENT,
    absolute_links,
    acceptable_facility,
    bento_snippets,
    clean_space,
    extract_record,
)
from location_hygiene import is_bad_facility_name, is_contaminated_record, person_names

SPREADSHEET_TITLE = "YUMI-CHA_ロケ地・お弁当マスター"
SHEET_NAME = "シート1"
HEADERS = ["カテゴリ", "名称", "所在地・エリア", "実績・概要", "情報元URL", "収集日時"]
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
KANTO = ("東京都", "神奈川県", "千葉県", "埼玉県", "茨城県", "栃木県", "群馬県")
BLOCKED_HOSTS = {"loca.ash.jp", "www.loca.ash.jp"}
CREDENTIALS_PATH = ROOT / "google-credentials.json"
PAUSE = 1.5

PHONE_RE = re.compile(r"(?:0\d{1,4}[-(]?\d{1,4}[-)]?\d{3,4}|0\d{9,10})")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
CIRCLED_RE = re.compile(r"[①-⑳⑴-⒇⓪]")
PAREN_SCENE_RE = re.compile(r"[（(\[［]\s*\d{1,4}\s*[）)\]］]")
SCENE_LABEL_RE = re.compile(r"(?:シーン番号|シーン|Scene|SCENE|＃|#|No\.?|NO\.?)\s*[:：.]?\s*\d{1,4}")
ISOLATED_NUM_RE = re.compile(r"(?:(?<=[\s　/／|｜、,・])|^)\d{1,3}(?=[\s　/／|｜、,・]|$)")
HOME_RE = re.compile(r"自宅|個人宅|実家|の家|中古物件|戸建|一軒家|表札|号室")
INTERNAL_RE = re.compile(
    r"制作部|連絡メモ|連絡網|挨拶回り|菓子折|香盤|仮払|精算|台本|決定稿|弓田|山田さん|村国|大川"
)
PUBLIC_RE = re.compile(
    r"公園|庁舎|公民館|文化会館|文化センター|体育館|図書館|博物館|美術館|公会堂|"
    r"市役所|区役所|都庁|県立|都立|市立|区立|公立|公共施設|公共の"
)
VENDOR_RE = re.compile(
    r"(?<![一-龥A-Za-z0-9])([一-龥A-Za-z0-9ー]{2,18}(?:弁当|仕出し|ケータリング))"
)
GENERIC_VENDOR = {"ロケ弁", "お弁当", "仕出し弁当", "弁当", "ケータリング", "仕出し"}
JST = timezone(timedelta(hours=9))


def authorize():
    raw = os.environ.get("GCP_SA_KEY", "").strip()
    if raw:
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit("GCP_SA_KEY のJSONを読めませんでした。") from exc
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        if not CREDENTIALS_PATH.is_file():
            raise SystemExit("GCP_SA_KEY が無く、google-credentials.json も見つかりません。")
        creds = Credentials.from_service_account_file(str(CREDENTIALS_PATH), scopes=SCOPES)
    return gspread.authorize(creds)


def open_sheet(client):
    book = client.open(SPREADSHEET_TITLE)
    try:
        return book.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        return book.sheet1


def norm_name(name: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name or "")).casefold()


def existing_names(worksheet) -> set[str]:
    column = worksheet.col_values(2)
    names = set()
    for index, value in enumerate(column):
        if index == 0 and clean_space(value) == "名称":
            continue
        key = norm_name(value)
        if key:
            names.add(key)
    return names


def ensure_header(worksheet):
    if clean_space(worksheet.acell("A1").value or ""):
        return
    worksheet.update(range_name="A1:F1", values=[HEADERS], value_input_option="USER_ENTERED")


def strip_scene_numbers(text: str) -> str:
    raw = CIRCLED_RE.sub("", text or "")
    raw = PAREN_SCENE_RE.sub("", raw)
    raw = SCENE_LABEL_RE.sub("", raw)
    raw = ISOLATED_NUM_RE.sub(" ", raw)
    return clean_space(raw)


def strip_contacts(text: str) -> str:
    raw = PHONE_RE.sub("", text or "")
    raw = EMAIL_RE.sub("", raw)
    for token in person_names(raw):
        raw = raw.replace(f"{token}さん", "")
    raw = INTERNAL_RE.sub("", raw)
    return clean_space(raw)


def private_place(name: str) -> bool:
    raw = name or ""
    if HOME_RE.search(raw):
        return True
    return "邸" in raw and "ビル" not in raw


def publishable(name: str, summary: str) -> bool:
    if not name or private_place(name) or private_place(summary):
        return False
    if PHONE_RE.search(name) or EMAIL_RE.search(name) or person_names(name):
        return False
    if INTERNAL_RE.search(name) or is_bad_facility_name(name):
        return False
    if is_contaminated_record(name, summary):
        return False
    if re.fullmatch(r"[\d\s]+", name):
        return False
    return True


def category_for(name: str, summary: str, source_name: str) -> str:
    blob = f"{name} {summary} {source_name}"
    if re.search(r"弁当|ケータリング|仕出し", name):
        return "ロケ弁"
    if PUBLIC_RE.search(blob):
        return "公共撮影施設"
    return "ロケ地"


def stamp() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def make_row(category: str, name: str, area: str, summary: str, url: str, when: str) -> list[str] | None:
    name = strip_scene_numbers(strip_contacts(name))[:80]
    area = strip_contacts(area)[:80]
    summary = strip_scene_numbers(strip_contacts(summary))[:180]
    if not publishable(name, summary):
        return None
    if not area:
        area = "関東"
    if not summary:
        summary = "公開ページに掲載"
    return [category, name, area, summary, url, when]


class PoliteFetcher:
    def __init__(self, pause: float):
        self.pause = pause
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.last_fetch = 0.0

    def pause_turn(self):
        wait = self.pause - (time.monotonic() - self.last_fetch)
        if wait > 0:
            time.sleep(wait)
        self.last_fetch = time.monotonic()

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or parsed.netloc in BLOCKED_HOSTS:
            return False
        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser = self.robots.get(origin)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            try:
                self.pause_turn()
                response = self.session.get(origin + "/robots.txt", timeout=20)
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
                if child and (not include or any(part.strip("/") in child for part in include)):
                    found.extend(self.sitemap_urls(child, include, seen))
            return found
        for node in root.findall("sm:url/sm:loc", SITEMAP_NS):
            loc = (node.text or "").strip()
            if loc and any(part in loc for part in include):
                found.append(loc)
        return found


def kanto_sources() -> list[dict]:
    return [source for source in SOURCES if source["prefecture"] in KANTO]


def page_targets(fetcher: PoliteFetcher, source: dict, limit: int) -> list[str]:
    include = tuple(source.get("include") or ())
    urls: list[str] = []
    if source.get("sitemap"):
        urls.extend(fetcher.sitemap_urls(source["sitemap"], include or ("/",)))
    else:
        urls.append(source["home"])
        try:
            html = fetcher.fetch(source["home"])
        except requests.RequestException as exc:
            print(f"fetch failed {source['home']}: {exc}")
            html = ""
        if html:
            soup = BeautifulSoup(html, "html.parser")
            host = urlparse(source["home"]).netloc
            news_include = include + ("news", "topics", "新着", "お知らせ", "public")
            urls.extend(absolute_links(soup, source["home"], host, news_include))
    deduped = []
    seen = set()
    for url in urls:
        if url not in seen and fetcher.allowed(url):
            seen.add(url)
            deduped.append(url)
    return deduped[:limit]


def vendor_rows(html: str, url: str, source: dict, when: str) -> list[list[str]]:
    rows = []
    for snippet in bento_snippets(html):
        cleaned = strip_contacts(snippet)
        for match in VENDOR_RE.finditer(cleaned):
            name = match.group(1)
            if name in GENERIC_VENDOR:
                continue
            row = make_row("ロケ弁", name, source["prefecture"], cleaned, url, when)
            if row:
                rows.append(row)
    return rows


def collect(limit: int, pause: float) -> list[list[str]]:
    fetcher = PoliteFetcher(pause)
    when = stamp()
    rows: list[list[str]] = []
    seen_names: set[str] = set()
    for source in kanto_sources():
        print(f"\n[{source['prefecture']}] {source['site_name']}")
        for url in page_targets(fetcher, source, limit):
            try:
                html = fetcher.fetch(url)
            except requests.RequestException as exc:
                print(f"fetch failed {url}: {exc}")
                continue
            if not html:
                continue
            record = extract_record(html, url, source)
            if record and acceptable_facility(record["location_name"], record["address"]):
                summary = record["scenes_category"] or "公開の撮影候補"
                row = make_row(
                    category_for(record["location_name"], summary, source["site_name"]),
                    record["location_name"],
                    record["address"] or source["prefecture"],
                    summary,
                    record["source_url"],
                    when,
                )
                if row and norm_name(row[1]) not in seen_names:
                    seen_names.add(norm_name(row[1]))
                    rows.append(row)
                    print(f"  候補: {row[0]} / {row[1]}")
            for row in vendor_rows(html, url, source, when):
                key = norm_name(row[1])
                if key in seen_names:
                    continue
                seen_names.add(key)
                rows.append(row)
                print(f"  候補: {row[0]} / {row[1]}")
    return rows


def append_new(worksheet, rows: list[list[str]]) -> int:
    known = existing_names(worksheet)
    fresh = []
    for row in rows:
        key = norm_name(row[1])
        if not key or key in known:
            continue
        known.add(key)
        fresh.append(row)
    if not fresh:
        return 0
    ensure_header(worksheet)
    worksheet.append_rows(fresh, value_input_option="USER_ENTERED")
    return len(fresh)


def main():
    parser = argparse.ArgumentParser(description="関東の公開ロケ地・ロケ弁をスプレッドシートへ差分追記する")
    parser.add_argument("--limit", type=int, default=6, help="各窓口で今回読むページ数")
    parser.add_argument("--pause", type=float, default=PAUSE, help="リクエスト間隔（秒）")
    parser.add_argument("--dry-run", action="store_true", help="収集だけ行い、シートには書かない")
    args = parser.parse_args()
    if args.limit < 1:
        raise SystemExit("--limit は 1 以上にしてください")
    rows = collect(args.limit, args.pause)
    print(f"\n収集 {len(rows)} 件")
    if args.dry_run:
        for row in rows:
            print("\t".join(row))
        return
    client = authorize()
    worksheet = open_sheet(client)
    added = append_new(worksheet, rows)
    print(f"追記 {added} 件（既存の名称はスキップ）")


if __name__ == "__main__":
    main()
