#!/usr/bin/env python3
"""過去作品フォルダを読み、ロケ地・お弁当・現場実務を台帳へ追記する。

画面や app.py は触らない。読み取り専用の資料から、現場で使う項目だけを抜き出す。
個人の口座、マイナンバー、個人の自宅は入れない。

例:
  python3 ingest_projects.py
  python3 ingest_projects.py "/Users/user/Desktop/past_works"
  python3 ingest_projects.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DIR = ROOT / "import_projects"
LOG_PATH = ROOT / "imported_log.json"
LOCATION_CSV = ROOT / "bulk_locations_master.csv"
SPREADSHEET_ID = "1wqzKFN5GqvLjURzJNZ_ypNOC1jaYfYlVj6eDlAcUH44"
SPREADSHEET_TITLE = "YUMI-CHA_ロケ地・お弁当マスター"
PLACE_SHEET = "ロケ地"
BENTO_SHEET = "お弁当"
VENDOR_SHEET = "現場実務・業者ナレッジ"
CSV_COLUMNS = ["ロケ地名", "住所", "エリア", "作品名", "シーン説明", "元URL"]
PLACE_HEADERS = [
    "ロケ地名",
    "住所",
    "条件（料金・時間・警察/施設ルール・駐車場等）",
    "シーン名",
    "ジャンル",
    "情報元（作品名/ファイル名）",
    "収集日時",
]
BENTO_HEADERS = [
    "店舗名",
    "ジャンル",
    "価格帯",
    "配達可能エリア",
    "早朝対応",
    "深夜対応",
    "空箱回収",
    "電話番号",
    "URL",
    "締切",
    "特徴",
    "提供元（作品名）",
]
VENDOR_HEADERS = [
    "カテゴリ",
    "項目・業者名",
    "エリア・連絡先",
    "料金・条件・実務ノウハウ",
    "ソース（作品名）",
]
EXTS = {".pdf", ".docx", ".xlsx", ".xls", ".csv", ".txt"}
SKIP_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
CHUNK_SIZE = 15000
CHUNK_LIMIT = 8
TEXT_LIMIT = CHUNK_SIZE * CHUNK_LIMIT
PAGE_LIMIT = 120
PAUSE = 1.2
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
JST = timezone(timedelta(hours=9))
PRIVATE_RE = re.compile(r"自宅|個人宅|実家|号室|表札")
PERSONAL_RE = re.compile(r"マイナンバー|個人番号|個人口座|給与振込|住民票|免許証番号|銀行口座")
ACCOUNT_RE = re.compile(r"口座")
EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@(?:gmail|yahoo|hotmail|outlook|icloud|me)\.(?:com|jp)",
    re.I,
)
AREA_RE = re.compile(
    r"((?:北海道|東京都|大阪府|京都府|[\u4e00-\u9fff]{2,3}県)"
    r"(?:[\u4e00-\u9fffA-Za-z0-9]{0,12}?[市区町村])?)"
)
GENERIC_NAMES = {"ロケ地", "場所", "公園", "店", "お弁当", "弁当", "業者", "不明", "なし", "要確認"}

INSTRUCTION = """あなたは映像制作の資料整理係です。渡された本文から、次の3種類だけをJSONで抜き出してください。
該当が無い種類は空配列にしてください。説明文、前置き、Markdownは書かないでください。

1. ロケ地: 施設・店舗・公園・道路など、撮影場所として読めるもの。キーは ロケ地名, 住所, 条件, シーン名, ジャンル。
   条件には本文にある料金、時間、警察や施設のルール、駐車場だけを短く書く。
2. お弁当: 仕出し・ケータリング・弁当店。キーは 店舗名, ジャンル, 価格帯, 配達可能エリア, 早朝対応, 深夜対応, 空箱回収, 電話番号, URL, 締切, 特徴。
3. 現場実務・業者ナレッジ: 業者、許可、交渉条件、車両、養生、予算の実務。キーは カテゴリ, 項目・業者名, エリア・連絡先, 料金・条件・実務ノウハウ。

禁止:
- スタッフ個人の銀行口座、マイナンバー、個人の自宅住所、個人の給与は出さない。
- 自宅、個人宅、実家、号室、表札が付く場所はロケ地にしない。
- 本文に無い料金、個数、電話番号、URL、最低注文個数は空欄。数字を作らない。
- 撮影日やセリフ、台本の全文は写さない。

出力の形:
{"ロケ地": [], "お弁当": [], "現場実務・業者ナレッジ": []}
"""


def clean(value: object, limit: int = 400) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = EMAIL_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" 　,、;；")
    return text[:limit]


def norm(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).casefold()


def stamp() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def grounded(value: str, source: str, limit: int = 120) -> str:
    text = clean(value, limit)
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 3 and digits not in re.sub(r"\D", "", unicodedata.normalize("NFKC", source)):
        return ""
    return text


def strip_invented_minimum(value: str, source: str) -> str:
    text = clean(value, 500)
    if "最低注文" in text and "最低注文" not in source:
        text = clean(re.sub(r"最低注文[^。]{0,24}", "", text), 500)
    return text


def personal(record: dict[str, str]) -> bool:
    blob = " ".join(record.values())
    if PERSONAL_RE.search(blob):
        return True
    if ACCOUNT_RE.search(blob) and re.search(r"\d{7,}", unicodedata.normalize("NFKC", blob)):
        return True
    return False


def usable_name(name: str) -> bool:
    text = clean(name, 80)
    if len(text) < 2 or text in GENERIC_NAMES:
        return False
    if PRIVATE_RE.search(text):
        return False
    return True


def source_label(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path(path.name)
    if len(relative.parts) >= 2:
        work = relative.parts[0]
    elif root.name not in {"import_projects", ".", ""}:
        work = root.name
    else:
        work = path.stem
    return clean(f"{work} / {path.name}", 120)


def area_of(address: str) -> str:
    found = AREA_RE.search(address or "")
    return found.group(1) if found else ""


def chunks_of(text: str) -> tuple[list[str], bool]:
    cleaned = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if not cleaned:
        return [], False
    parts: list[str] = []
    start = 0
    truncated = len(cleaned) > TEXT_LIMIT
    while start < len(cleaned) and len(parts) < CHUNK_LIMIT:
        end = min(len(cleaned), start + CHUNK_SIZE)
        if end < len(cleaned):
            split = cleaned.rfind("\n", start + CHUNK_SIZE // 2, end)
            if split > start:
                end = split
        piece = cleaned[start:end].strip()
        if piece:
            parts.append(piece)
        start = end
    if start < len(cleaned):
        truncated = True
    return parts, truncated


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    try:
        load_dotenv(ROOT / ".env")
    except Exception:
        return


def model_names() -> list[str]:
    chain = ["gemini-flash-lite-latest"]
    extra = os.getenv("GEMINI_MODEL", "").strip()
    if extra and extra not in chain:
        chain.append(extra)
    return chain


def response_text(response) -> str:
    text = getattr(response, "text", None) or ""
    if str(text).strip():
        return str(text)
    chunks = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            piece = getattr(part, "text", None)
            if piece:
                chunks.append(piece)
    return "\n".join(chunks)


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


def pick_rows(data: dict, *keys: str) -> list[dict]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def field(row: dict, *keys: str) -> str:
    for key in keys:
        if key in row and str(row.get(key) or "").strip():
            return str(row.get(key) or "")
    return ""


def clean_locations(data: dict, source_text: str, origin: str, when: str) -> list[dict[str, str]]:
    rows = []
    for item in pick_rows(data, "ロケ地", "locations"):
        name = clean(field(item, "ロケ地名", "名称", "name"), 80)
        address = clean(field(item, "住所", "address"), 160)
        if not usable_name(name) or PRIVATE_RE.search(address):
            continue
        record = {
            "ロケ地名": name,
            "住所": address,
            "条件": strip_invented_minimum(field(item, "条件", "conditions"), source_text),
            "シーン名": clean(field(item, "シーン名", "scene"), 80),
            "ジャンル": clean(field(item, "ジャンル", "genre"), 40),
            "情報元": origin,
            "収集日時": when,
        }
        if personal(record):
            continue
        rows.append(record)
    return rows


def clean_bento(data: dict, source_text: str, origin: str) -> list[dict[str, str]]:
    rows = []
    for item in pick_rows(data, "お弁当", "bento"):
        name = clean(field(item, "店舗名", "name"), 80)
        if not usable_name(name):
            continue
        record = {
            "店舗名": name,
            "ジャンル": clean(field(item, "ジャンル", "genre"), 40),
            "価格帯": grounded(field(item, "価格帯", "price"), source_text, 80),
            "配達可能エリア": clean(field(item, "配達可能エリア", "area"), 120),
            "早朝対応": clean(field(item, "早朝対応", "early"), 40),
            "深夜対応": clean(field(item, "深夜対応", "late"), 40),
            "空箱回収": clean(field(item, "空箱回収", "box"), 40),
            "電話番号": grounded(field(item, "電話番号", "phone"), source_text, 40),
            "URL": grounded(field(item, "URL", "url"), source_text, 180),
            "締切": grounded(field(item, "締切", "deadline"), source_text, 80),
            "特徴": strip_invented_minimum(field(item, "特徴", "feature"), source_text),
            "提供元": origin,
        }
        if personal(record) or PRIVATE_RE.search(name):
            continue
        rows.append(record)
    return rows


def clean_vendor(data: dict, source_text: str, origin: str) -> list[dict[str, str]]:
    rows = []
    for item in pick_rows(data, "現場実務・業者ナレッジ", "knowledge", "ナレッジ"):
        name = clean(field(item, "項目・業者名", "業者名", "項目", "name"), 80)
        if not usable_name(name):
            continue
        record = {
            "カテゴリ": clean(field(item, "カテゴリ", "category"), 40),
            "項目・業者名": name,
            "エリア・連絡先": clean(field(item, "エリア・連絡先", "連絡先", "contact"), 160),
            "料金・条件・実務ノウハウ": strip_invented_minimum(
                field(item, "料金・条件・実務ノウハウ", "ノウハウ", "note"),
                source_text,
            ),
            "ソース": origin,
        }
        if personal(record):
            continue
        rows.append(record)
    return rows


def dedupe(rows: list[dict[str, str]], key: str) -> list[dict[str, str]]:
    kept = []
    seen = set()
    for row in rows:
        token = norm(row.get(key, ""))
        if not token or token in seen:
            continue
        seen.add(token)
        kept.append(row)
    return kept


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_log() -> dict:
    if not LOG_PATH.is_file():
        return {"files": {}}
    try:
        data = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("取り込み記録を読めなかったため、新しい記録で続けます。")
        return {"files": {}}
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {"files": {}}
    return {"files": files}


def save_log(data: dict) -> None:
    temporary = LOG_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(LOG_PATH)


def collect_files(root: Path) -> list[Path]:
    found = []
    if not root.exists():
        return found
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.name.startswith("~$") or path.name.casefold() in SKIP_NAMES:
            continue
        if path.suffix.casefold() not in EXTS:
            continue
        found.append(path)
    found.sort(key=lambda item: str(item).casefold())
    return found


def read_plain(path: Path) -> str:
    raw = path.read_bytes()[: TEXT_LIMIT * 2]
    for encoding in ("utf-8-sig", "cp932", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        opened = 0
        try:
            opened = reader.decrypt("")
        except Exception as exc:
            raise RuntimeError("password") from exc
        if opened == 0:
            raise RuntimeError("password")
    pages = []
    for index, page in enumerate(reader.pages):
        if index >= PAGE_LIMIT:
            break
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            continue
        if sum(len(part) for part in pages) >= TEXT_LIMIT:
            break
    return "\n".join(pages)


def read_docx(path: Path) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("python-docx がありません。python3 -m pip install python-docx") from exc
    document = Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables[:20]:
        for row in table.rows[:400]:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" ".join(cells))
        if sum(len(part) for part in parts) >= TEXT_LIMIT:
            break
    return "\n".join(parts)


def read_table(path: Path) -> str:
    import pandas as pd

    suffix = path.suffix.casefold()
    try:
        if suffix == ".csv":
            frame = pd.read_csv(path, dtype=str, nrows=3000)
            frames = {"csv": frame}
        else:
            engine = "xlrd" if suffix == ".xls" else None
            frames = pd.read_excel(path, sheet_name=None, dtype=str, nrows=3000, engine=engine)
    except Exception:
        if suffix == ".csv":
            return read_plain(path)
        raise
    lines = []
    for name, frame in list(frames.items())[:8]:
        lines.append(f"# {name}")
        filled = frame.fillna("")
        for row in filled.astype(str).values.tolist()[:3000]:
            cells = [clean(cell, 80) for cell in row if clean(cell, 80)]
            if cells:
                lines.append(" ".join(cells))
            if sum(len(line) for line in lines) >= TEXT_LIMIT:
                return "\n".join(lines)
    return "\n".join(lines)


def read_document(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix in {".txt", ".csv"} and suffix == ".txt":
        return read_plain(path)
    if suffix == ".csv":
        return read_table(path)
    if suffix == ".pdf":
        return read_pdf(path)
    if suffix == ".docx":
        return read_docx(path)
    if suffix in {".xlsx", ".xls"}:
        return read_table(path)
    raise RuntimeError("未対応の形式")


def short_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "password" in lowered or "encrypt" in lowered:
        return "パスワード付きのためスキップ"
    if "python-docx" in text:
        return "Wordを読む部品がありません。python3 -m pip install python-docx を実行してください"
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "通信が混み合っているため、このファイルは次回に回します"
    return "読めなかったためスキップ"


def ask_model(client, excerpt: str) -> dict:
    from google.genai import types

    prompt = INSTRUCTION + "\n\n【資料】\n" + excerpt
    last: Exception | None = None
    configs = [
        types.GenerateContentConfig(
            system_instruction="出力はJSONオブジェクトのみ。",
            temperature=0.1,
            max_output_tokens=8192,
            response_mime_type="application/json",
        ),
        types.GenerateContentConfig(
            system_instruction="出力はJSONオブジェクトのみ。",
            temperature=0.1,
            max_output_tokens=4096,
        ),
    ]
    for model in model_names():
        for config in configs:
            for attempt in range(3):
                try:
                    response = client.models.generate_content(model=model, contents=prompt, config=config)
                    time.sleep(PAUSE)
                    raw = response_text(response)
                    payload = parse_payload(raw)
                    if not payload:
                        raise ValueError("json")
                    return payload
                except Exception as exc:
                    last = exc
                    time.sleep(PAUSE * (attempt + 1))
    if last is not None:
        raise last
    return {}


def analyze_text(client, text: str, origin: str) -> tuple[list[dict], list[dict], list[dict], bool]:
    parts, truncated = chunks_of(text)
    when = stamp()
    locations: list[dict[str, str]] = []
    bento: list[dict[str, str]] = []
    vendor: list[dict[str, str]] = []
    for part in parts:
        data = ask_model(client, part)
        locations.extend(clean_locations(data, part, origin, when))
        bento.extend(clean_bento(data, part, origin))
        vendor.extend(clean_vendor(data, part, origin))
    return (
        dedupe(locations, "ロケ地名"),
        dedupe(bento, "店舗名"),
        dedupe(vendor, "項目・業者名"),
        truncated,
    )


def header_has(cell: str, key: str) -> bool:
    return key.casefold() in str(cell or "").casefold()


def column_index(header: list[str], keys: tuple[str, ...], fallback: int) -> int:
    for index, cell in enumerate(header):
        if any(header_has(cell, key) for key in keys):
            return index
    return fallback


def aligned_row(header: list[str], pairs: list[tuple[tuple[str, ...], str]]) -> list[str]:
    width = max(len(header), 1)
    row = [""] * width
    used = set()
    for keys, value in pairs:
        for index, cell in enumerate(header):
            if index in used:
                continue
            if any(header_has(cell, key) for key in keys):
                row[index] = value
                used.add(index)
                break
    return row


def place_pairs(row: dict[str, str]) -> list[tuple[tuple[str, ...], str]]:
    return [
        (("ロケ地名",), row.get("ロケ地名", "")),
        (("住所", "所在地"), row.get("住所", "")),
        (("条件",), row.get("条件", "")),
        (("シーン名",), row.get("シーン名", "")),
        (("ジャンル",), row.get("ジャンル", "")),
        (("情報元", "作品名"), row.get("情報元", "")),
        (("収集日時", "日時"), row.get("収集日時", "")),
    ]


def bento_pairs(row: dict[str, str]) -> list[tuple[tuple[str, ...], str]]:
    return [
        (("店舗名",), row.get("店舗名", "")),
        (("ジャンル",), row.get("ジャンル", "")),
        (("価格帯",), row.get("価格帯", "")),
        (("配達可能エリア", "配達"), row.get("配達可能エリア", "")),
        (("早朝対応", "早朝"), row.get("早朝対応", "")),
        (("深夜対応", "深夜"), row.get("深夜対応", "")),
        (("空箱回収", "空箱"), row.get("空箱回収", "")),
        (("電話番号", "電話"), row.get("電話番号", "")),
        (("URL", "url"), row.get("URL", "")),
        (("締切",), row.get("締切", "")),
        (("特徴",), row.get("特徴", "")),
        (("提供元",), row.get("提供元", "")),
    ]


def vendor_pairs(row: dict[str, str]) -> list[tuple[tuple[str, ...], str]]:
    return [
        (("カテゴリ",), row.get("カテゴリ", "")),
        (("項目・業者名", "業者名", "項目"), row.get("項目・業者名", "")),
        (("エリア・連絡先", "連絡先"), row.get("エリア・連絡先", "")),
        (("料金・条件・実務ノウハウ", "実務ノウハウ", "ノウハウ"), row.get("料金・条件・実務ノウハウ", "")),
        (("ソース",), row.get("ソース", "")),
    ]


def location_csv_row(row: dict[str, str]) -> dict[str, str]:
    address = row.get("住所", "")
    return {
        "ロケ地名": row.get("ロケ地名", ""),
        "住所": address,
        "エリア": area_of(address),
        "作品名": row.get("情報元", ""),
        "シーン説明": row.get("シーン名", ""),
        "元URL": "",
    }


def open_book():
    import gspread
    from google.oauth2.service_account import Credentials

    raw = os.getenv("GCP_SA_KEY", "").strip()
    if raw:
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            print("スプレッドシートの認証情報を読めませんでした。")
            return None
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    elif (ROOT / "google-credentials.json").is_file():
        creds = Credentials.from_service_account_file(str(ROOT / "google-credentials.json"), scopes=SCOPES)
    else:
        print("スプレッドシートの認証がありません。GCP_SA_KEY か google-credentials.json を用意してから再実行してください。")
        return None
    client = gspread.authorize(creds)
    try:
        return client.open_by_key(SPREADSHEET_ID)
    except Exception:
        return client.open(SPREADSHEET_TITLE)


def ensure_sheet(book, title: str, headers: list[str]):
    import gspread

    try:
        worksheet = book.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = book.add_worksheet(title=title, rows=5000, cols=max(len(headers), 7))
        time.sleep(PAUSE)
        worksheet.update(range_name="A1", values=[headers], value_input_option="RAW")
        time.sleep(PAUSE)
        return worksheet
    current = worksheet.row_values(1)
    if not any(str(cell or "").strip() for cell in current):
        worksheet.update(range_name="A1", values=[headers], value_input_option="RAW")
        time.sleep(PAUSE)
    return worksheet


def retry_call(action):
    for attempt in range(4):
        try:
            return action()
        except Exception as exc:
            if attempt == 3 or "429" not in str(exc):
                raise
            time.sleep(PAUSE * (2 ** attempt))
    return None


def existing_names(worksheet, keys: tuple[str, ...], fallback: int) -> set[str]:
    header = retry_call(lambda: worksheet.row_values(1)) or []
    index = column_index(header, keys, fallback)
    values = retry_call(lambda: worksheet.col_values(index + 1)) or []
    found = set()
    for cell in values[1:]:
        token = norm(cell)
        if token and token != norm(header[index] if index < len(header) else ""):
            found.add(token)
    return found


def append_rows(worksheet, rows: list[list[str]]) -> None:
    import gspread

    for start in range(0, len(rows), 40):
        batch = rows[start : start + 40]
        for attempt in range(4):
            try:
                worksheet.append_rows(batch, value_input_option="RAW")
                break
            except gspread.exceptions.APIError as exc:
                if attempt == 3 or "429" not in str(exc):
                    raise
                time.sleep(PAUSE * (2 ** attempt))
        time.sleep(PAUSE)


def csv_names() -> set[str]:
    if not LOCATION_CSV.is_file():
        return set()
    found = set()
    with LOCATION_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            token = norm(str(raw.get("ロケ地名") or ""))
            if token:
                found.add(token)
    return found


def append_location_csv(rows: list[dict[str, str]], known: set[str]) -> int:
    fresh = []
    for row in rows:
        token = norm(row.get("ロケ地名", ""))
        if not token or token in known:
            continue
        fresh.append(location_csv_row(row))
    if not fresh:
        return 0
    new_file = not LOCATION_CSV.is_file() or LOCATION_CSV.stat().st_size == 0
    mode = "w" if new_file else "a"
    encoding = "utf-8-sig" if new_file else "utf-8"
    with LOCATION_CSV.open(mode, encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerows(fresh)
    for row in fresh:
        known.add(norm(row.get("ロケ地名", "")))
    return len(fresh)


def remember(log: dict, digest: str, path: Path, status: str, counts: dict, pending: dict | None) -> None:
    entry = {
        "path": str(path.resolve()),
        "sha256": digest,
        "at": stamp(),
        "status": status,
        "locations": counts.get("locations", 0),
        "bento": counts.get("bento", 0),
        "knowledge": counts.get("knowledge", 0),
    }
    if pending:
        entry["pending"] = pending
    log["files"][digest] = entry
    save_log(log)


def named_rows(header: list[str], rows: list[list[str]], keys: tuple[str, ...]) -> list[list[str]]:
    index = column_index(header, keys, -1)
    if index < 0:
        return []
    return [row for row in rows if index < len(row) and str(row[index]).strip()]


def publish(book, cache: dict, locations, bento, vendor, dry_run: bool) -> tuple[bool, str]:
    sheet_locations = [row for row in locations if norm(row["ロケ地名"]) not in cache["place_names"]]
    sheet_bento = [row for row in bento if norm(row["店舗名"]) not in cache["bento_names"]]
    sheet_vendor = [row for row in vendor if norm(row["項目・業者名"]) not in cache["vendor_names"]]
    if dry_run:
        print(f"  確認のみ: ロケ地{len(sheet_locations)}件 / 弁当{len(sheet_bento)}件 / ナレッジ{len(sheet_vendor)}件")
        return True, ""
    csv_count = 0
    try:
        csv_count = append_location_csv(locations, cache["csv_names"])
        if sheet_locations:
            pairs = named_rows(
                cache["place_header"],
                [aligned_row(cache["place_header"], place_pairs(row)) for row in sheet_locations],
                ("ロケ地名",),
            )
            if pairs:
                append_rows(cache["place"], pairs)
                cache["place_names"].update(norm(row["ロケ地名"]) for row in sheet_locations)
        if sheet_bento:
            pairs = named_rows(
                cache["bento_header"],
                [aligned_row(cache["bento_header"], bento_pairs(row)) for row in sheet_bento],
                ("店舗名",),
            )
            if pairs:
                append_rows(cache["bento"], pairs)
                cache["bento_names"].update(norm(row["店舗名"]) for row in sheet_bento)
        if sheet_vendor:
            pairs = named_rows(
                cache["vendor_header"],
                [aligned_row(cache["vendor_header"], vendor_pairs(row)) for row in sheet_vendor],
                ("項目・業者名", "業者名", "項目"),
            )
            if pairs:
                append_rows(cache["vendor"], pairs)
                cache["vendor_names"].update(norm(row["項目・業者名"]) for row in sheet_vendor)
    except Exception as exc:
        print(f"  台帳への追記に失敗しました（{short_error(exc)}）")
        return False, short_error(exc)
    if csv_count or sheet_locations or sheet_bento or sheet_vendor:
        print(
            f"  登録: ロケ地{len(sheet_locations)}件 / 弁当{len(sheet_bento)}件 / ナレッジ{len(sheet_vendor)}件"
            f"（辞書へ{csv_count}件）"
        )
    return True, ""


def prepare_sheets(book) -> dict:
    place = ensure_sheet(book, PLACE_SHEET, PLACE_HEADERS)
    bento = ensure_sheet(book, BENTO_SHEET, BENTO_HEADERS)
    vendor = ensure_sheet(book, VENDOR_SHEET, VENDOR_HEADERS)
    place_header = place.row_values(1) or PLACE_HEADERS
    bento_header = bento.row_values(1) or BENTO_HEADERS
    vendor_header = vendor.row_values(1) or VENDOR_HEADERS
    print("既存の名前を照合しています...")
    return {
        "place": place,
        "bento": bento,
        "vendor": vendor,
        "place_header": place_header,
        "bento_header": bento_header,
        "vendor_header": vendor_header,
        "place_names": existing_names(place, ("ロケ地名",), 0),
        "bento_names": existing_names(bento, ("店舗名",), 0),
        "vendor_names": existing_names(vendor, ("項目・業者名", "業者名", "項目"), 1),
        "csv_names": csv_names(),
    }


def make_client():
    from google import genai

    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        print("GEMINI_API_KEY が .env にありません。設定してから再実行してください。")
        return None
    return genai.Client(api_key=key)


def stored_rows(entry: dict) -> tuple[list, list, list] | None:
    pending = entry.get("pending")
    if not isinstance(pending, dict):
        return None
    return (
        list(pending.get("ロケ地") or []),
        list(pending.get("お弁当") or []),
        list(pending.get("現場実務・業者ナレッジ") or []),
    )


def run(root: Path, dry_run: bool, limit: int) -> int:
    root.mkdir(parents=True, exist_ok=True)
    files = collect_files(root)
    if limit:
        files = files[:limit]
    if not files:
        print(f"対象ファイルがありません。{root} に資料を入れて再実行してください。")
        return 0
    log = load_log()
    client = None
    book = None
    cache = None
    total = len(files)
    for index, path in enumerate(files, start=1):
        try:
            digest = file_digest(path)
        except OSError:
            print(f"[{index}/{total}] {path.name} は読めなかったためスキップ")
            continue
        entry = log["files"].get(digest) or {}
        if entry.get("status") == "done" or entry.get("status") == "skipped":
            print(f"[{index}/{total}] {path.name} は取り込み済みのためスキップ")
            continue
        origin = source_label(path, root)
        locations: list[dict] = []
        bento: list[dict] = []
        vendor: list[dict] = []
        reused = stored_rows(entry)
        if reused is not None:
            locations, bento, vendor = reused
            print(f"[{index}/{total}] {path.name} は解析済みのため、台帳への登録だけやり直します")
        else:
            if path.stat().st_size > 40 * 1024 * 1024:
                print(f"[{index}/{total}] {path.name} は大きすぎるためスキップ")
                remember(log, digest, path, "skipped", {}, None)
                continue
            print(f"[{index}/{total}] {path.name} を解析中...", flush=True)
            try:
                text = read_document(path)
            except Exception as exc:
                print(f"[{index}/{total}] {path.name} は{short_error(exc)}")
                if "次回に回します" in short_error(exc):
                    continue
                remember(log, digest, path, "skipped", {}, None)
                continue
            if not clean(text, 20):
                print(f"[{index}/{total}] {path.name} を解析中... ロケ地0件 / 弁当0件 / ナレッジ0件抽出")
                if not dry_run:
                    remember(log, digest, path, "done", {}, None)
                continue
            if client is None:
                client = make_client()
                if client is None:
                    return 1
            try:
                locations, bento, vendor, truncated = analyze_text(client, text, origin)
            except Exception as exc:
                print(f"[{index}/{total}] {path.name} は{short_error(exc)}")
                continue
            print(
                f"[{index}/{total}] {path.name} を解析中... "
                f"ロケ地{len(locations)}件 / 弁当{len(bento)}件 / ナレッジ{len(vendor)}件抽出"
            )
            if truncated:
                print("  長い資料のため、先頭部分だけを見ました。")
        counts = {"locations": len(locations), "bento": len(bento), "knowledge": len(vendor)}
        pending_payload = {"ロケ地": locations, "お弁当": bento, "現場実務・業者ナレッジ": vendor}
        if not locations and not bento and not vendor:
            if not dry_run:
                remember(log, digest, path, "done", counts, None)
            continue
        if book is None and not dry_run:
            book = open_book()
            if book is None:
                remember(log, digest, path, "pending", counts, pending_payload)
                return 1
            try:
                cache = prepare_sheets(book)
            except Exception as exc:
                print(f"既存データの照合に失敗しました（{short_error(exc)}）")
                remember(log, digest, path, "pending", counts, pending_payload)
                return 1
        if dry_run:
            cache = cache or {
                "place_names": set(),
                "bento_names": set(),
                "vendor_names": set(),
                "csv_names": set(),
                "place_header": PLACE_HEADERS,
                "bento_header": BENTO_HEADERS,
                "vendor_header": VENDOR_HEADERS,
                "place": None,
                "bento": None,
                "vendor": None,
            }
        ok, _message = publish(book, cache, locations, bento, vendor, dry_run)
        if dry_run:
            continue
        if ok:
            remember(log, digest, path, "done", counts, None)
        else:
            remember(
                log,
                digest,
                path,
                "pending",
                counts,
                {"ロケ地": locations, "お弁当": bento, "現場実務・業者ナレッジ": vendor},
            )
    print("取り込みが終わりました。")
    return 0


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="過去作品フォルダをスプレッドシートとCSVへ取り込む")
    parser.add_argument("folder", nargs="?", default="", help="資料フォルダ。省略時は import_projects")
    parser.add_argument("--dry-run", action="store_true", help="登録せず、抽出結果の件数だけを見る")
    parser.add_argument("--limit", type=int, default=0, help="処理するファイル数の上限")
    args = parser.parse_args()
    root = Path(args.folder).expanduser().resolve() if args.folder else DEFAULT_DIR
    if args.folder and not root.exists():
        print(f"フォルダが見つかりません: {root}")
        return 1
    return run(root, args.dry_run, max(args.limit, 0))


if __name__ == "__main__":
    raise SystemExit(main())
