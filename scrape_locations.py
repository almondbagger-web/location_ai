import csv
import html
import json
import re
import time
import urllib.parse
import warnings
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

LOCA_INDEX = "https://loca.ash.jp/indexnew.htm"
FUMI_INDEX = "https://fumichen2.livedoor.blog/"
CSV_PATH = "scraped_locations_sample.csv"
JSON_PATH = "scraped_locations_sample.json"

LOCA_LIMIT = 16
LOCA_PER_PAGE = 8
FUMI_LIMIT = 12
REQUEST_INTERVAL = 1.5

USER_AGENT = "location-ai-sample/1.0 (personal location research)"
DISALLOW = {
    "loca.ash.jp": ("/cgi/", "/staff/", "/member/", "/admin/", "/guide/", "/view/", "/board/"),
    "view.ash.jp": ("/cgi/", "/ap/", "/js/map/"),
}
FIELDS = [
    "サイト",
    "ロケ地名",
    "住所",
    "シーン・設定・施設カテゴリ",
    "関連作品情報",
    "GoogleマップURL",
    "詳細",
    "情報元URL",
]
CATEGORY_WORDS = [
    "学校", "大学", "高校", "中学校", "小学校", "病院", "オフィス", "刑務所",
    "カフェ", "屋上", "神社", "寺", "公園", "駅", "橋", "ホール", "スタジオ",
    "マンション", "住宅", "ホテル", "美術館", "博物館", "教会", "礼拝", "階段",
    "工場", "結婚式場", "動物公園", "劇場", "校舎",
]
PREFECTURE_RE = re.compile(
    r"(北海道|東京都|大阪府|京都府|[\u4e00-\u9fff]{2,3}県)"
)
ADDRESS_RE = re.compile(
    r"(?:〒\s*\d{3}-?\d{4}\s*)?"
    r"((?:北海道|東京都|大阪府|京都府|[\u4e00-\u9fff]{2,3}県(?!立))"
    r"[\u4e00-\u9fff]{1,10}?[市区町村郡].{0,40})"
)

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})
last_request_at = 0.0


def allowed(url):
    parsed = urlparse(url)
    path = parsed.path or "/"
    for prefix in DISALLOW.get(parsed.netloc, ()):
        if path.startswith(prefix):
            return False
    return True


def decode_response(response):
    content_type = response.headers.get("Content-Type", "")
    match = re.search(r"charset=[\"']?([^\"';\s]+)", content_type, re.I)
    if match:
        encoding = match.group(1).lower()
        if encoding in {"shift_jis", "sjis", "shift-jis"}:
            encoding = "cp932"
        response.encoding = encoding
        return response.text
    head = response.content[:8000]
    match = re.search(br"charset=[\"']?([A-Za-z0-9_\-]+)", head, re.I)
    if match:
        response.encoding = match.group(1).decode("ascii", "ignore")
        return response.text
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def fetch(url):
    global last_request_at
    if not allowed(url):
        raise RuntimeError(f"robots.txt で取得しないURLです: {url}")
    wait = REQUEST_INTERVAL - (time.time() - last_request_at)
    if last_request_at and wait > 0:
        time.sleep(wait)
    response = session.get(url, timeout=30)
    last_request_at = time.time()
    response.raise_for_status()
    return decode_response(response)


def soup_of(text):
    return BeautifulSoup(text, "lxml")


def categories_in(*texts):
    blob = " ".join(text for text in texts if text)
    found = [word for word in CATEGORY_WORDS if word in blob]
    return "、".join(dict.fromkeys(found))


def maps_url(lat, lng, address):
    if lat is not None and lng is not None:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    if address:
        query = urllib.parse.quote(address)
        return f"https://www.google.com/maps/search/?api=1&query={query}"
    return ""


def js_string(body, key):
    match = re.search(rf"\b{key}\s*:\s*(['\"])(.*?)(?<!\\)\1", body, re.S)
    if not match:
        return ""
    value = match.group(2).replace(r"\'", "'").replace(r"\"", '"')
    return html.unescape(value).strip()


def js_float(body, key):
    match = re.search(rf"\b{key}\s*:\s*(-?\d+(?:\.\d+)?)", body)
    if not match:
        return None
    return float(match.group(1))


def extract_js_info(page):
    records = []
    for match in re.finditer(r"jsInfo\[\d+\]\s*=\s*\{", page):
        start = match.end() - 1
        depth = 0
        end = None
        for index, char in enumerate(page[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end is None:
            continue
        body = page[start + 1:end]
        if re.search(r"\brestrict\s*:\s*1\b", body):
            continue
        name = js_string(body, "name")
        scene = js_string(body, "title")
        locaaddr = js_string(body, "locaaddr")
        note = js_string(body, "lvinfo")
        if not name and not locaaddr:
            continue
        address = locaaddr
        if "：" in locaaddr:
            address = locaaddr.split("：", 1)[1].strip()
        records.append({
            "name": name,
            "scene": scene,
            "address": address,
            "note": note,
            "lat": js_float(body, "lat"),
            "lng": js_float(body, "lng"),
        })
    return records


def work_summary(page_soup):
    info = {}
    for row in page_soup.select("table.drama_info tr"):
        header = row.find("th")
        cell = row.find("td")
        if header and cell:
            info[header.get_text(strip=True)] = re.sub(r"\s+", " ", cell.get_text("", strip=True))
    parts = [info.get("作品名", ""), info.get("制作年", "")]
    return " ".join(part for part in parts if part).strip()


def latest_loca_works(index_html):
    links = []
    for href in re.findall(r'href="(info/\d{4}/[^"]+\.htm)"', index_html):
        url = urljoin("https://loca.ash.jp/", href)
        if url not in links and allowed(url):
            links.append(url)
    return links


def scrape_loca():
    print("全国ロケ地ガイド: 更新順一覧を取得")
    works = latest_loca_works(fetch(LOCA_INDEX))
    rows = []
    for work_url in works:
        if len(rows) >= LOCA_LIMIT:
            break
        print(f"  作品ページ: {work_url}")
        page = fetch(work_url)
        page_soup = soup_of(page)
        work = work_summary(page_soup)
        taken = 0
        for item in extract_js_info(page):
            if len(rows) >= LOCA_LIMIT or taken >= LOCA_PER_PAGE:
                break
            scene_bits = [item["scene"], categories_in(item["name"], item["scene"], item["address"])]
            detail_bits = [item["scene"], item["address"], item["note"]]
            rows.append({
                "サイト": "全国ロケ地ガイド",
                "ロケ地名": item["name"],
                "住所": item["address"],
                "シーン・設定・施設カテゴリ": " / ".join(bit for bit in scene_bits if bit),
                "関連作品情報": work,
                "GoogleマップURL": maps_url(item["lat"], item["lng"], item["address"]),
                "詳細": " / ".join(bit for bit in detail_bits if bit),
                "情報元URL": work_url,
            })
            taken += 1
    return rows


def article_links(page_html, page_url):
    page_soup = soup_of(page_html)
    links = []
    for anchor in page_soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"]).split("#")[0]
        path = urlparse(href).path
        if re.fullmatch(r"/archives/\d+\.html", path) and href not in links:
            links.append(href)
    return links


def visible_lines(article):
    lines = []
    for node in article.find_all("div"):
        if node.find("div"):
            continue
        text = node.get_text("\n", strip=True)
        for line in text.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                lines.append(line)
    return lines


def is_address(line):
    return bool(ADDRESS_RE.search(line)) and "http" not in line


def place_before(lines, index):
    for cursor in range(index - 1, -1, -1):
        line = lines[cursor]
        if line.startswith("http") or is_address(line):
            continue
        if "。" in line or line.startswith("http"):
            continue
        if 1 < len(line) <= 60:
            return line
    return ""


def split_fumi_places(lines, title):
    places = []
    for index, line in enumerate(lines):
        match = ADDRESS_RE.search(line)
        if not match or "http" in line:
            continue
        address = match.group(1).strip()
        name = place_before(lines, index) or title
        block = lines[max(0, index - 3):index + 2]
        places.append((name, address, " / ".join(block)))
    if places:
        return places
    return [(title, "", " / ".join(lines))]


def scrape_fumi():
    print("fumi Diary 2号店: 記事一覧を取得")
    page_url = FUMI_INDEX
    links = []
    while len(links) < FUMI_LIMIT and page_url:
        page = fetch(page_url)
        for link in article_links(page, page_url):
            if link not in links:
                links.append(link)
        page_soup = soup_of(page)
        next_link = page_soup.find("a", rel="next") or page_soup.select_one("a.next")
        page_url = urljoin(page_url, next_link["href"]) if next_link and next_link.get("href") else ""
        if not page_url or len(links) >= FUMI_LIMIT:
            break
    rows = []
    for article_url in links[:FUMI_LIMIT]:
        print(f"  記事: {article_url}")
        page = fetch(article_url)
        page_soup = soup_of(page)
        title_node = page_soup.select_one(".article-title")
        title = title_node.get_text(" ", strip=True) if title_node else page_soup.title.get_text(strip=True)
        date_node = page_soup.select_one(".article-date")
        posted = date_node.get_text(" ", strip=True) if date_node else ""
        categories = "、".join(anchor.get_text(strip=True) for anchor in page_soup.select(".article-category a"))
        article = page_soup.select_one("div.article-body")
        lines = visible_lines(article) if article else []
        detail = "\n".join(lines)
        work = " ".join(part for part in (title, posted, categories) if part)
        for name, address, block in split_fumi_places(lines, title):
            scene = " / ".join(
                bit for bit in (categories_in(name, block, title), categories) if bit
            )
            rows.append({
                "サイト": "fumi Diary 2号店",
                "ロケ地名": name,
                "住所": address,
                "シーン・設定・施設カテゴリ": scene,
                "関連作品情報": work,
                "GoogleマップURL": maps_url(None, None, address),
                "詳細": detail or block,
                "情報元URL": article_url,
            })
    return rows


def write_outputs(rows):
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: str(row.get(key, "")).replace("\r\n", "\n").replace("\r", "\n").replace("\n", " / ")
                for key in FIELDS
            })
    with open(JSON_PATH, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main():
    rows = scrape_loca() + scrape_fumi()
    write_outputs(rows)
    loca_count = sum(1 for row in rows if row["サイト"] == "全国ロケ地ガイド")
    fumi_count = sum(1 for row in rows if row["サイト"] == "fumi Diary 2号店")
    print(f"保存: {CSV_PATH} / {JSON_PATH}")
    print(f"全国ロケ地ガイド {loca_count} 件 / fumi Diary 2号店 {fumi_count} 件 / 合計 {len(rows)} 件")


if __name__ == "__main__":
    main()
