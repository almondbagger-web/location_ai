#!/usr/bin/env python3
"""関東FC・最新ドラマ/CMロケ地を収集し、ロケ地台帳へ差分追記する。

東京ロケーションボックス以外の公開リストと、放送実績系サイトも対象。
Gemini が使えるときは構造化し、使えないときはヒューリスティックで落とし込む。

例:
  python3 crawl_drama_locations.py --limit 40 --dry-run
  python3 crawl_drama_locations.py --limit 80
  python3 crawl_drama_locations.py --skip-gemini --limit 40
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
LOCATION_CSV = ROOT / "bulk_locations_master.csv"
URL_LOG = ROOT / "collected_drama_urls.txt"
SPREADSHEET_ID = "1wqzKFN5GqvLjURzJNZ_ypNOC1jaYfYlVj6eDlAcUH44"
SPREADSHEET_TITLE = "YUMI-CHA_ロケ地・お弁当マスター"
PLACE_SHEET = "ロケ地"
PLACE_HEADERS = [
    "ロケ地名",
    "住所",
    "条件（料金・時間・警察/施設ルール・駐車場等）",
    "シーン名",
    "ジャンル",
    "情報元（作品名/ファイル名）",
    "収集日時",
]
CSV_COLUMNS = ["ロケ地名", "住所", "エリア", "作品名", "シーン説明", "元URL"]
SHEET_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
USER_AGENT = "YUMI-CHA-drama-crawler/1.0 (location research; contact via almond bagger)"
PAUSE = 0.6
JST = timezone(timedelta(hours=9))
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

PRIVATE_RE = re.compile(r"自宅|個人宅|実家|表札|号室")
PHONE_RE = re.compile(r"0\d{1,4}[-(]?\d{1,4}[-)]?\d{3,4}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
AREA_RE = re.compile(
    r"((?:北海道|東京都|大阪府|京都府|[\u4e00-\u9fff]{2,3}県)"
    r"(?:[\u4e00-\u9fffA-Za-z0-9]{0,12}?[市区町村])?)"
)
GENERIC = {"ロケ地", "場所", "公園", "施設", "一覧", "検索", "トップ", "ホーム", "お知らせ"}
SKIP_TITLE_RE = re.compile(
    r"検索|一覧|トップ|ホーム|について|お問い合わせ|募集|助成|English|ホームページ|"
    r"公開放映|カテゴリ|ロケ地情報:|フィルムコミッション$|サポートデスク"
)

SOURCES = [
    {
        "name": "東京ロケーションボックス",
        "home": "https://www.locationbox.metro.tokyo.lg.jp/catalog/",
        "include": ("/catalog/",),
        "kind": "fc",
    },
    {
        "name": "横浜フィルムコミッション",
        "home": "https://www.city.yokohama.lg.jp/kanko-bunka/miryoku/films/",
        "include": ("films", "location", "ロケ"),
        "kind": "fc",
    },
    {
        "name": "かわさきロケ情報",
        "home": "https://www.kawasaki-location.org/location/public.html",
        "include": ("/location/", "public"),
        "kind": "fc",
    },
    {
        "name": "神奈川ロケーションサポートデスク",
        "home": "https://www.pref.kanagawa.jp/docs/b6m/cnt/f80022/p811391.html",
        "include": ("location", "film", "ロケ"),
        "kind": "fc",
    },
    {
        "name": "埼玉県ロケーションサービス",
        "home": "https://chocotabi-saitama.jp/business/location-top/",
        "include": ("location", "ロケ", "business"),
        "kind": "fc",
    },
    {
        "name": "千葉県フィルムコミッション",
        "home": "https://fc.ccb.or.jp/location/",
        "include": ("/location",),
        "kind": "fc",
    },
    {
        "name": "かずさアークフィルムコミッション",
        "home": "https://www.kap.co.jp/film/",
        "include": ("/film", "ロケ", "撮影"),
        "kind": "fc",
    },
    {
        "name": "木更津市観光・ロケ案内",
        "home": "https://www.city.kisarazu.lg.jp/",
        "include": ("観光", "ロケ", "撮影", "film"),
        "kind": "fc",
    },
    {
        "name": "湘南藤沢フィルム・コミッション",
        "home": "http://www.shonanfujisawa.jp/",
        "include": ("location", "ロケ", "film"),
        "kind": "fc",
    },
    {
        "name": "撮影NAVI",
        "home": "https://satsuei-navi.com/",
        "include": ("/location", "/spot", "/studio", "ロケ"),
        "kind": "drama",
    },
    {
        "name": "ロカナビ",
        "home": "https://locanavi.com/",
        "include": ("/location", "/spot", "ロケ"),
        "kind": "drama",
    },
    {
        "name": "全国ロケ地ガイド新着",
        "home": "https://loca.ash.jp/",
        "include": ("/location", "/spot", "/drama", "/movie"),
        "kind": "drama",
    },
]

INSTRUCTION = """あなたは映像制作のロケ地整理係です。渡された公開ページ本文から撮影場所だけをJSONで抜き出してください。
該当が無ければ空配列。説明文やMarkdownは書かないでください。

各要素のキー:
施設名, エリア, 住所, カテゴリ, 撮影実績, 車両条件メモ, 情報元URL

禁止:
- 個人の自宅、号室、表札
- 電話番号・メールアドレス
- 本文に無い料金や数字の捏造

出力:
{"locations": []}
"""


def clean(value: object, limit: int = 400) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = EMAIL_RE.sub("", text)
    text = PHONE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" 　,、;；")
    return text[:limit]


def norm(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).casefold()


def stamp() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:
        return


def session() -> requests.Session:
    handle = requests.Session()
    handle.headers.update({"User-Agent": USER_AGENT})
    return handle


def fetch(handle: requests.Session, url: str, pause: float) -> str:
    time.sleep(max(pause, 0.2))
    response = handle.get(url, timeout=25)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def absolute(base: str, href: str) -> str:
    return urljoin(base, href)


def page_links(html: str, base: str, include: tuple[str, ...]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()
    for tag in soup.find_all("a", href=True):
        href = absolute(base, tag["href"])
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        if "#" in href:
            href = href.split("#", 1)[0]
        blob = href + " " + clean(tag.get_text(" ", strip=True), 80)
        if include and not any(token in blob for token in include):
            continue
        if href in seen:
            continue
        seen.add(href)
        found.append(href)
    return found


def plain_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return clean(soup.get_text("\n", strip=True), 12000)


def title_of(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return clean(soup.title.string, 120)
    heading = soup.find(["h1", "h2"])
    return clean(heading.get_text(" ", strip=True), 120) if heading else ""


def load_url_log() -> set[str]:
    if not URL_LOG.is_file():
        return set()
    return {line.strip() for line in URL_LOG.read_text(encoding="utf-8").splitlines() if line.strip()}


def save_url_log(urls: set[str]) -> None:
    URL_LOG.write_text("\n".join(sorted(urls)) + "\n", encoding="utf-8")


def heuristic_record(html: str, url: str, source_name: str) -> dict[str, str] | None:
    title = title_of(html)
    text = plain_text(html)
    if not title or title in GENERIC or PRIVATE_RE.search(title):
        return None
    if len(title) < 2 or SKIP_TITLE_RE.search(title):
        return None
    # 「施設名 | サイト名」形式は左側を採用
    if "|" in title:
        title = clean(title.split("|", 1)[0], 80)
    if " - " in title:
        title = clean(title.split(" - ", 1)[0], 80)
    if not title or title in GENERIC or SKIP_TITLE_RE.search(title) or len(title) < 3:
        return None
    area = ""
    found = AREA_RE.search(text)
    if found:
        area = found.group(1)
    address = ""
    addr = re.search(
        r"((?:北海道|東京都|大阪府|京都府|[\u4e00-\u9fff]{2,3}県)[^\n。]{4,40})",
        text,
    )
    if addr:
        address = clean(addr.group(1), 160)
    work = ""
    work_match = re.search(r"(?:作品|ドラマ|映画|CM)[:：]?\s*([^\n。]{2,40})", text)
    if work_match:
        work = clean(work_match.group(1), 80)
    vehicle = ""
    for key in ("駐車場", "車両", "搬入", "バス", "待機", "許可"):
        if key in text:
            snippet = re.search(rf".{{0,20}}{key}.{{0,40}}", text)
            if snippet:
                vehicle = clean(snippet.group(0), 120)
                break
    category = "ドラマロケ地" if any(token in text for token in ("ドラマ", "映画", "CM")) else "公開ロケ地"
    if PRIVATE_RE.search(address):
        return None
    return {
        "ロケ地名": title,
        "住所": address or area,
        "エリア": area or (AREA_RE.search(address).group(1) if AREA_RE.search(address) else ""),
        "条件": vehicle,
        "シーン名": "汎用撮影",
        "ジャンル": category,
        "撮影実績": work,
        "情報元": clean(f"{source_name} / {work or title}", 120),
        "情報元URL": url,
        "収集日時": stamp(),
    }


def parse_payload(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def gemini_records(html: str, url: str, source_name: str) -> list[dict[str, str]]:
    from google import genai
    from google.genai import types

    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return []
    client = genai.Client(api_key=key)
    model = os.getenv("GEMINI_MODEL", "").strip() or "gemini-flash-lite-latest"
    excerpt = plain_text(html)[:9000]
    prompt = INSTRUCTION + f"\n\n【情報元】{source_name}\n【URL】{url}\n【本文】\n{excerpt}"
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=4096,
                response_mime_type="application/json",
            ),
        )
    except Exception as exc:
        print(f"  Geminiスキップ: {exc}")
        return []
    text = getattr(response, "text", None) or ""
    data = parse_payload(text)
    rows = data.get("locations") if isinstance(data.get("locations"), list) else []
    out = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = clean(item.get("施設名") or item.get("名称") or item.get("name"), 80)
        if not name or name in GENERIC or PRIVATE_RE.search(name):
            continue
        address = clean(item.get("住所") or item.get("address"), 160)
        if PRIVATE_RE.search(address):
            continue
        area = clean(item.get("エリア") or item.get("area"), 80)
        if not area and address:
            found = AREA_RE.search(address)
            area = found.group(1) if found else ""
        record = {
            "ロケ地名": name,
            "住所": address or area,
            "エリア": area,
            "条件": clean(item.get("車両条件メモ") or item.get("条件") or item.get("memo"), 200),
            "シーン名": clean(item.get("カテゴリ") or item.get("シーン") or "汎用撮影", 80),
            "ジャンル": clean(item.get("カテゴリ") or "公開ロケ地", 40),
            "撮影実績": clean(item.get("撮影実績") or item.get("作品名"), 80),
            "情報元": clean(f"{source_name} / {item.get('撮影実績') or name}", 120),
            "情報元URL": clean(item.get("情報元URL") or url, 240),
            "収集日時": stamp(),
        }
        out.append(record)
    return out


def collect_from_source(
    handle: requests.Session,
    source: dict,
    limit: int,
    pause: float,
    known_urls: set[str],
    use_gemini: bool,
) -> list[dict[str, str]]:
    home = source["home"]
    print(f"収集中: {source['name']} ({home})")
    try:
        html = fetch(handle, home, pause)
    except Exception as exc:
        print(f"  入口取得失敗: {exc}")
        return []
    links = page_links(html, home, tuple(source.get("include") or ()))
    if home not in links:
        links.insert(0, home)
    records: list[dict[str, str]] = []
    for link in links:
        if len(records) >= limit:
            break
        if link in known_urls:
            continue
        try:
            page = fetch(handle, link, pause)
        except Exception:
            continue
        known_urls.add(link)
        found: list[dict[str, str]] = []
        if use_gemini:
            found = gemini_records(page, link, source["name"])
        if not found:
            one = heuristic_record(page, link, source["name"])
            if one:
                found = [one]
        for row in found:
            if not row.get("ロケ地名"):
                continue
            records.append(row)
            if len(records) >= limit:
                break
        print(f"  +{len(found)} {link[:90]}")
    return records


def open_book():
    import gspread
    from google.oauth2.service_account import Credentials

    raw = os.getenv("GCP_SA_KEY", "").strip()
    if raw:
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=SHEET_SCOPES)
    elif (ROOT / "google-credentials.json").is_file():
        creds = Credentials.from_service_account_file(str(ROOT / "google-credentials.json"), scopes=SHEET_SCOPES)
    else:
        print("スプレッドシート認証がありません。")
        return None
    client = gspread.authorize(creds)
    try:
        return client.open_by_key(SPREADSHEET_ID)
    except Exception:
        return client.open(SPREADSHEET_TITLE)


def ensure_place_sheet(book):
    import gspread

    try:
        worksheet = book.worksheet(PLACE_SHEET)
    except gspread.WorksheetNotFound:
        worksheet = book.add_worksheet(title=PLACE_SHEET, rows=5000, cols=len(PLACE_HEADERS))
        worksheet.update(range_name="A1", values=[PLACE_HEADERS], value_input_option="RAW")
        return worksheet
    head = worksheet.row_values(1)
    if not any(str(cell or "").strip() for cell in head):
        worksheet.update(range_name="A1", values=[PLACE_HEADERS], value_input_option="RAW")
    return worksheet


def existing_names(worksheet) -> set[str]:
    values = worksheet.col_values(1) or []
    return {norm(cell) for cell in values[1:] if clean(cell, 2)}


def append_sheet(worksheet, rows: list[dict[str, str]], known: set[str]) -> int:
    batch = []
    for row in rows:
        token = norm(row["ロケ地名"])
        if not token or token in known:
            continue
        batch.append(
            [
                row.get("ロケ地名", ""),
                row.get("住所", ""),
                row.get("条件", ""),
                row.get("シーン名", "") or row.get("撮影実績", "") or "汎用撮影",
                row.get("ジャンル", ""),
                row.get("情報元", ""),
                row.get("収集日時", stamp()),
            ]
        )
        known.add(token)
    if not batch:
        return 0
    for start in range(0, len(batch), 40):
        worksheet.append_rows(batch[start : start + 40], value_input_option="RAW")
        time.sleep(0.8)
    return len(batch)


def append_csv(rows: list[dict[str, str]]) -> int:
    known = set()
    if LOCATION_CSV.is_file():
        with LOCATION_CSV.open(encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                known.add(norm(str(raw.get("ロケ地名") or "")))
    fresh = []
    for row in rows:
        token = norm(row.get("ロケ地名", ""))
        if not token or token in known:
            continue
        fresh.append(
            {
                "ロケ地名": row.get("ロケ地名", ""),
                "住所": row.get("住所", ""),
                "エリア": row.get("エリア", ""),
                "作品名": row.get("撮影実績") or row.get("情報元", ""),
                "シーン説明": row.get("シーン名", ""),
                "元URL": row.get("情報元URL", ""),
            }
        )
        known.add(token)
    if not fresh:
        return 0
    new_file = not LOCATION_CSV.is_file() or LOCATION_CSV.stat().st_size == 0
    with LOCATION_CSV.open("a" if not new_file else "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerows(fresh)
    return len(fresh)


def run(limit: int, pause: float, dry_run: bool, skip_gemini: bool) -> int:
    load_env()
    handle = session()
    known_urls = load_url_log()
    use_gemini = not skip_gemini
    collected: list[dict[str, str]] = []
    per = max(3, limit // max(len(SOURCES), 1))
    for source in SOURCES:
        if len(collected) >= limit:
            break
        remain = min(per, limit - len(collected))
        rows = collect_from_source(handle, source, remain, pause, known_urls, use_gemini)
        collected.extend(rows)
    # 名称で最終重複除去
    unique = []
    seen = set()
    for row in collected:
        token = norm(row.get("ロケ地名", ""))
        if not token or token in seen:
            continue
        seen.add(token)
        unique.append(row)
    print(f"抽出 {len(unique)} 件")
    save_url_log(known_urls)
    if dry_run:
        for row in unique[:20]:
            print(f"- {row.get('ロケ地名')} / {row.get('住所')} / {row.get('情報元')}")
        return 0
    csv_count = append_csv(unique)
    print(f"CSV追記 {csv_count} 件")
    book = open_book()
    if book is None:
        return 1
    sheet = ensure_place_sheet(book)
    known = existing_names(sheet)
    added = append_sheet(sheet, unique, known)
    print(f"シート「ロケ地」追記 {added} 件")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="関東FC・ドラマロケ地を台帳へ取り込む")
    parser.add_argument("--limit", type=int, default=60, help="1回の最大件数")
    parser.add_argument("--pause", type=float, default=PAUSE, help="リクエスト間隔秒")
    parser.add_argument("--dry-run", action="store_true", help="書き込まず件数確認のみ")
    parser.add_argument("--skip-gemini", action="store_true", help="Geminiを使わずヒューリスティック抽出")
    args = parser.parse_args()
    return run(max(args.limit, 1), max(args.pause, 0.2), args.dry_run, args.skip_gemini)


if __name__ == "__main__":
    raise SystemExit(main())
