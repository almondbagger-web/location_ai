#!/usr/bin/env python3
"""Gmail本文と添付ファイルから現場実務データ・文章ナレッジを抽出し、台帳へ追記する。

画面や既存の制作部ノウハウは触らない。未処理メールの差分だけを解析する。

例:
  python3 ingest_gmail.py
  python3 ingest_gmail.py --days 365 --limit 20
  python3 ingest_gmail.py --query "subject:弁当 newer_than:90d" --dry-run
"""

from __future__ import annotations

import argparse
import base64
import csv
import html as html_lib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CREDENTIALS_CANDIDATES = [
    ROOT / "gmail_credentials.json",
    ROOT / "gmail_credentials.json.json",
]
TOKEN_PATH = ROOT / "gmail_token.json"
PROCESSED_PATH = ROOT / "processed_gmail_ids.json"
LOCATION_CSV = ROOT / "bulk_locations_master.csv"
SPREADSHEET_ID = "1wqzKFN5GqvLjURzJNZ_ypNOC1jaYfYlVj6eDlAcUH44"
SPREADSHEET_TITLE = "YUMI-CHA_ロケ地・お弁当マスター"
PLACE_SHEET = "ロケ地"
BENTO_SHEET = "お弁当"
VENDOR_SHEET = "現場実務・業者ナレッジ"
WRITING_SHEET = "文章ナレッジ"
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
WRITING_HEADERS = [
    "用途・シーン",
    "件名例",
    "本文テンプレート",
    "交渉・配慮のポイント",
    "情報元",
]
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SHEET_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
JST = timezone(timedelta(hours=9))
CHUNK_SIZE = 15000
CHUNK_LIMIT = 8
TEXT_LIMIT = CHUNK_SIZE * CHUNK_LIMIT
PAGE_LIMIT = 120
PAUSE = 1.2
ATTACH_EXTS = {".pdf", ".xlsx", ".xls", ".docx"}
DEFAULT_DAYS = 180
DEFAULT_KEYWORDS = ("ロケ", "撮影", "香盤表", "弁当", "制作", "スタジオ", "許可", "控室", "集合")

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
GENERIC_NAMES = {
    "ロケ地",
    "場所",
    "公園",
    "店",
    "お弁当",
    "弁当",
    "業者",
    "不明",
    "なし",
    "要確認",
    "控室",
    "ベース",
}

INSTRUCTION = """あなたは映像制作の資料整理係です。渡されたメール本文と添付テキストから、次の5種類だけをJSONで抜き出してください。
該当が無い種類は空配列にしてください。説明文、前置き、Markdownは書かないでください。
メルマガ・広告・現場業務と無関係な内容はすべて空配列にしてください。

1. ロケ地: 施設・店舗・公園・道路・スタジオなど撮影場所。キーは ロケ地名, 住所, 条件, シーン名, ジャンル。
   条件には料金、時間、警察や施設のルール、駐車場・車両メモ、注意事項だけを短く書く。
2. 支度場所・控室・ベース: キーは 施設名, 住所, 収容規模, 備考。
3. お弁当: 仕出し・ケータリング・弁当店。キーは 店舗名, ジャンル, 価格帯, 配達可能エリア, 早朝対応, 深夜対応, 空箱回収, 電話番号, URL, 締切, 特徴。
4. 現場の注意事項・連絡先・許可窓口: キーは カテゴリ, 項目・業者名, エリア・連絡先, 料金・条件・実務ノウハウ。
   撮影許可窓口、NG事項、特記事項をここに入れる。
5. 文章ナレッジ: 実務メールの丁寧な言い回し・構成パターン・定型フレーズ。
   キーは 用途・シーン, 件名例, 本文テンプレート, 交渉・配慮のポイント。
   個人名・固有の施設名・電話番号・メールアドレス・具体的な作品名・撮影日は〔施設名〕〔担当者名〕〔日付〕などの汎用プレースホルダに置き換える。
   ロケ打診、許可申請、条件交渉、お弁当発注、集合案内など再利用できる文面だけを残す。

禁止:
- スタッフ個人の銀行口座、マイナンバー、個人の自宅住所、個人の給与は出さない。
- 自宅、個人宅、実家、号室、表札が付く場所はロケ地にしない。
- 本文に無い料金、個数、電話番号、URL、最低注文個数は空欄。数字を作らない。

出力の形:
{"ロケ地": [], "支度場所・控室・ベース": [], "お弁当": [], "現場の注意事項・連絡先・許可窓口": [], "文章ナレッジ": []}
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


def short_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "password" in lowered or "encrypt" in lowered:
        return "パスワード付きのためスキップ"
    if "python-docx" in text:
        return "Wordを読む部品がありません"
    if "401" in text or "unauthenticated" in lowered or "account_state_invalid" in lowered:
        return "Gemini APIキーが無効です。.env の GEMINI_API_KEY を新しいキーに差し替えてください"
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "通信が混み合っているため、次回に回します"
    if "credentials" in lowered or "oauth" in lowered:
        return "Gmail認証に失敗しました"
    return "読めなかったためスキップ"


def is_gemini_auth_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "401" in text or "unauthenticated" in text or "account_state_invalid" in text


def default_query(days: int) -> str:
    words = " OR ".join(DEFAULT_KEYWORDS)
    return f"newer_than:{max(days, 1)}d ({words} OR has:attachment)"


def load_processed() -> dict:
    if not PROCESSED_PATH.is_file():
        return {"ids": {}}
    try:
        data = json.loads(PROCESSED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("処理済みIDを読めなかったため、新しい記録で続けます。")
        return {"ids": {}}
    ids = data.get("ids") if isinstance(data, dict) else None
    if not isinstance(ids, dict):
        return {"ids": {}}
    return {"ids": ids}


def save_processed(data: dict) -> None:
    temporary = PROCESSED_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(PROCESSED_PATH)


def remember(log: dict, message_id: str, status: str, counts: dict, meta: dict | None = None) -> None:
    entry = {
        "at": stamp(),
        "status": status,
        "locations": counts.get("locations", 0),
        "dressing": counts.get("dressing", 0),
        "bento": counts.get("bento", 0),
        "notes": counts.get("notes", 0),
        "writing": counts.get("writing", 0),
    }
    if meta:
        entry.update(meta)
    log["ids"][message_id] = entry
    save_processed(log)


def find_credentials() -> Path | None:
    for path in CREDENTIALS_CANDIDATES:
        if path.is_file():
            return path
    return None


def gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    cred_path = find_credentials()
    if cred_path is None:
        print(
            "gmail_credentials.json がありません。"
            "Google Cloud の OAuth クライアント秘密鍵を同ディレクトリに置いてください。"
        )
        return None
    creds = None
    if TOKEN_PATH.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), GMAIL_SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), GMAIL_SCOPES)
        creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def decode_body(data: str) -> bytes:
    raw = (data or "").replace("-", "+").replace("_", "/")
    padding = "=" * (-len(raw) % 4)
    try:
        return base64.urlsafe_b64decode(raw + padding)
    except Exception:
        return b""


def html_to_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"(?i)</div>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", text)).strip()


def walk_parts(payload: dict) -> list[dict]:
    found = []
    if not isinstance(payload, dict):
        return found
    found.append(payload)
    for part in payload.get("parts") or []:
        found.extend(walk_parts(part))
    return found


def extract_body_text(payload: dict) -> str:
    plain_chunks: list[str] = []
    html_chunks: list[str] = []
    for part in walk_parts(payload):
        mime = str(part.get("mimeType") or "").lower()
        filename = str(part.get("filename") or "").strip()
        body = part.get("body") or {}
        data = body.get("data")
        if filename or not data:
            continue
        raw = decode_body(data)
        if not raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            try:
                text = raw.decode("cp932")
            except UnicodeError:
                text = raw.decode("utf-8", errors="replace")
        if mime.startswith("text/plain"):
            plain_chunks.append(text)
        elif mime.startswith("text/html"):
            html_chunks.append(html_to_text(text))
    if plain_chunks:
        return "\n\n".join(chunk.strip() for chunk in plain_chunks if chunk.strip())
    return "\n\n".join(chunk.strip() for chunk in html_chunks if chunk.strip())


def header_map(headers: list[dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in headers or []:
        name = str(item.get("name") or "").strip().lower()
        if name and name not in result:
            result[name] = str(item.get("value") or "")
    return result


def parse_mail_date(value: str) -> str:
    if not value:
        return datetime.now(JST).strftime("%Y-%m-%d")
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(JST).strftime("%Y-%m-%d")


def attachment_ext(filename: str, mime: str) -> str:
    name = (filename or "").casefold()
    suffix = Path(name).suffix
    if suffix in ATTACH_EXTS:
        return suffix
    mime = (mime or "").lower()
    if "pdf" in mime:
        return ".pdf"
    if "spreadsheet" in mime or "excel" in mime:
        return ".xlsx"
    if "wordprocessingml" in mime or "msword" in mime:
        return ".docx"
    return ""


def read_pdf_bytes(raw: bytes) -> str:
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(raw))
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


def read_docx_bytes(raw: bytes) -> str:
    from io import BytesIO

    from docx import Document

    document = Document(BytesIO(raw))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables[:20]:
        for row in table.rows[:400]:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" ".join(cells))
        if sum(len(part) for part in parts) >= TEXT_LIMIT:
            break
    return "\n".join(parts)


def read_xlsx_bytes(raw: bytes) -> str:
    from io import BytesIO

    from openpyxl import load_workbook

    book = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    lines = []
    for sheet in book.worksheets[:8]:
        lines.append(f"# {sheet.title}")
        for index, row in enumerate(sheet.iter_rows(values_only=True)):
            if index >= 3000:
                break
            cells = [clean(cell, 80) for cell in row if clean(cell, 80)]
            if cells:
                lines.append(" ".join(cells))
            if sum(len(line) for line in lines) >= TEXT_LIMIT:
                return "\n".join(lines)
    return "\n".join(lines)


def extract_attachment_text(raw: bytes, ext: str) -> str:
    if ext == ".pdf":
        return read_pdf_bytes(raw)
    if ext == ".docx":
        return read_docx_bytes(raw)
    if ext in {".xlsx", ".xls"}:
        return read_xlsx_bytes(raw)
    return ""


def collect_attachments(service, message_id: str, payload: dict) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    for part in walk_parts(payload):
        filename = str(part.get("filename") or "").strip()
        mime = str(part.get("mimeType") or "")
        body = part.get("body") or {}
        ext = attachment_ext(filename, mime)
        if not filename and not ext:
            continue
        if not ext:
            continue
        attach_id = body.get("attachmentId")
        raw = b""
        try:
            if attach_id:
                result = (
                    service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=message_id, id=attach_id)
                    .execute()
                )
                raw = decode_body(result.get("data") or "")
            elif body.get("data"):
                raw = decode_body(body.get("data") or "")
        except Exception as exc:
            print(f"    添付 {filename or ext} は{short_error(exc)}")
            continue
        if not raw:
            continue
        try:
            text = extract_attachment_text(raw, ext)
        except Exception as exc:
            print(f"    添付 {filename or ext} は{short_error(exc)}")
            continue
        if clean(text, 20):
            texts.append((filename or f"attachment{ext}", text))
    return texts


def list_message_ids(service, query: str, limit: int) -> list[str]:
    ids: list[str] = []
    page_token = None
    while True:
        request = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=min(100, max(limit - len(ids), 1) if limit else 100), pageToken=page_token)
        )
        result = request.execute()
        for item in result.get("messages") or []:
            mid = str(item.get("id") or "").strip()
            if mid:
                ids.append(mid)
            if limit and len(ids) >= limit:
                return ids
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return ids


def fetch_message(service, message_id: str) -> dict:
    return (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )


def build_mail_corpus(service, message: dict) -> tuple[str, str, dict]:
    payload = message.get("payload") or {}
    headers = header_map(payload.get("headers") or [])
    subject = clean(headers.get("subject", ""), 200) or "(件名なし)"
    sender = clean(headers.get("from", ""), 200)
    date_label = parse_mail_date(headers.get("date", ""))
    body = extract_body_text(payload)
    attachments = collect_attachments(service, str(message.get("id") or ""), payload)
    blocks = [
        f"件名: {subject}",
        f"送信日時: {date_label}",
        f"送信元: {sender}",
        "",
        "【本文】",
        body or "(本文なし)",
    ]
    for name, text in attachments:
        blocks.extend(["", f"【添付: {name}】", text])
    origin = f"Gmail（{date_label} / {subject}）"
    meta = {"subject": subject, "date": date_label, "from": sender, "attachments": len(attachments)}
    return "\n".join(blocks), origin, meta


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
            "条件": strip_invented_minimum(field(item, "条件", "conditions", "駐車場", "注意事項"), source_text),
            "シーン名": clean(field(item, "シーン名", "scene"), 80),
            "ジャンル": clean(field(item, "ジャンル", "genre"), 40) or "ロケ地",
            "情報元": origin,
            "収集日時": when,
        }
        if personal(record):
            continue
        rows.append(record)
    return rows


def clean_dressing(data: dict, source_text: str, origin: str, when: str) -> list[dict[str, str]]:
    rows = []
    for item in pick_rows(data, "支度場所・控室・ベース", "控室", "dressing", "base"):
        name = clean(field(item, "施設名", "名称", "name"), 80)
        address = clean(field(item, "住所", "address"), 160)
        if not usable_name(name) or PRIVATE_RE.search(address):
            continue
        capacity = clean(field(item, "収容規模", "capacity"), 80)
        note = strip_invented_minimum(field(item, "備考", "note"), source_text)
        condition = " / ".join(part for part in (capacity, note) if part)
        record = {
            "ロケ地名": name,
            "住所": address,
            "条件": condition,
            "シーン名": "控室・ベース",
            "ジャンル": "控室・ベース",
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
            "ジャンル": clean(field(item, "ジャンル", "genre", "メニュー"), 40),
            "価格帯": grounded(field(item, "価格帯", "price"), source_text, 80),
            "配達可能エリア": clean(field(item, "配達可能エリア", "配達条件", "area"), 120),
            "早朝対応": clean(field(item, "早朝対応", "early"), 40),
            "深夜対応": clean(field(item, "深夜対応", "late"), 40),
            "空箱回収": clean(field(item, "空箱回収", "box"), 40),
            "電話番号": grounded(field(item, "電話番号", "連絡先", "phone"), source_text, 40),
            "URL": grounded(field(item, "URL", "url"), source_text, 180),
            "締切": grounded(field(item, "締切", "deadline"), source_text, 80),
            "特徴": strip_invented_minimum(field(item, "特徴", "評価メモ", "feature"), source_text),
            "提供元": origin,
        }
        if personal(record) or PRIVATE_RE.search(name):
            continue
        rows.append(record)
    return rows


def clean_notes(data: dict, source_text: str, origin: str) -> list[dict[str, str]]:
    rows = []
    for item in pick_rows(
        data,
        "現場の注意事項・連絡先・許可窓口",
        "注意事項",
        "notes",
        "許可窓口",
    ):
        name = clean(field(item, "項目・業者名", "業者名", "項目", "窓口", "name"), 80)
        if not usable_name(name):
            continue
        record = {
            "カテゴリ": clean(field(item, "カテゴリ", "category"), 40) or "注意事項・許可窓口",
            "項目・業者名": name,
            "エリア・連絡先": clean(field(item, "エリア・連絡先", "連絡先", "contact"), 160),
            "料金・条件・実務ノウハウ": strip_invented_minimum(
                field(item, "料金・条件・実務ノウハウ", "NG事項", "特記事項", "ノウハウ", "note"),
                source_text,
            ),
            "ソース": origin,
        }
        if personal(record):
            continue
        rows.append(record)
    return rows


def clean_writing(data: dict, origin: str) -> list[dict[str, str]]:
    rows = []
    for item in pick_rows(data, "文章ナレッジ", "writing", "templates"):
        scene = clean(field(item, "用途・シーン", "用途", "シーン", "scene"), 120)
        subject = clean(field(item, "件名例", "件名", "subject"), 160)
        body = clean(field(item, "本文テンプレート", "本文", "template", "body"), 2000)
        tips = clean(field(item, "交渉・配慮のポイント", "ポイント", "tips"), 500)
        if not scene and not body:
            continue
        if len(body) < 20 and len(scene) < 4:
            continue
        record = {
            "用途・シーン": scene or "実務メール",
            "件名例": subject,
            "本文テンプレート": body,
            "交渉・配慮のポイント": tips,
            "情報元": origin,
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


def dedupe_writing(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    kept = []
    seen = set()
    for row in rows:
        token = norm(row.get("用途・シーン", "") + "|" + row.get("件名例", "") + "|" + row.get("本文テンプレート", "")[:80])
        if not token or token in seen:
            continue
        seen.add(token)
        kept.append(row)
    return kept


def ask_model(client, excerpt: str) -> dict:
    from google.genai import types

    prompt = INSTRUCTION + "\n\n【メール資料】\n" + excerpt
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


def analyze_text(client, text: str, origin: str) -> tuple[list, list, list, list, list, bool]:
    parts, truncated = chunks_of(text)
    when = stamp()
    locations: list[dict[str, str]] = []
    dressing: list[dict[str, str]] = []
    bento: list[dict[str, str]] = []
    notes: list[dict[str, str]] = []
    writing: list[dict[str, str]] = []
    for part in parts:
        data = ask_model(client, part)
        locations.extend(clean_locations(data, part, origin, when))
        dressing.extend(clean_dressing(data, part, origin, when))
        bento.extend(clean_bento(data, part, origin))
        notes.extend(clean_notes(data, part, origin))
        writing.extend(clean_writing(data, origin))
    return (
        dedupe(locations, "ロケ地名"),
        dedupe(dressing, "ロケ地名"),
        dedupe(bento, "店舗名"),
        dedupe(notes, "項目・業者名"),
        dedupe_writing(writing),
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


def writing_pairs(row: dict[str, str]) -> list[tuple[tuple[str, ...], str]]:
    return [
        (("用途・シーン", "用途", "シーン"), row.get("用途・シーン", "")),
        (("件名例", "件名"), row.get("件名例", "")),
        (("本文テンプレート", "本文", "テンプレート"), row.get("本文テンプレート", "")),
        (("交渉・配慮のポイント", "ポイント", "交渉"), row.get("交渉・配慮のポイント", "")),
        (("情報元",), row.get("情報元", "")),
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
        creds = Credentials.from_service_account_info(info, scopes=SHEET_SCOPES)
    elif (ROOT / "google-credentials.json").is_file():
        creds = Credentials.from_service_account_file(str(ROOT / "google-credentials.json"), scopes=SHEET_SCOPES)
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


def existing_origins(worksheet, keys: tuple[str, ...] = ("情報元", "ソース", "提供元")) -> set[str]:
    header = retry_call(lambda: worksheet.row_values(1)) or []
    index = column_index(header, keys, -1)
    if index < 0:
        return set()
    values = retry_call(lambda: worksheet.col_values(index + 1)) or []
    found = set()
    for cell in values[1:]:
        token = norm(cell)
        if token:
            found.add(token)
    return found


def existing_writing_keys(worksheet) -> set[str]:
    values = retry_call(lambda: worksheet.get_all_values()) or []
    if not values:
        return set()
    header = values[0]
    scene_i = column_index(header, ("用途・シーン", "用途", "シーン"), 0)
    subject_i = column_index(header, ("件名例", "件名"), 1)
    origin_i = column_index(header, ("情報元",), 4)
    found = set()
    for row in values[1:]:
        scene = row[scene_i] if scene_i < len(row) else ""
        subject = row[subject_i] if subject_i < len(row) else ""
        origin = row[origin_i] if origin_i < len(row) else ""
        token = norm(f"{scene}|{subject}|{origin}")
        if token:
            found.add(token)
        if origin:
            found.add(norm(origin + "|" + scene))
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


def named_rows(header: list[str], rows: list[list[str]], keys: tuple[str, ...]) -> list[list[str]]:
    index = column_index(header, keys, -1)
    if index < 0:
        return []
    return [row for row in rows if index < len(row) and str(row[index]).strip()]


def prepare_sheets(book) -> dict:
    place = ensure_sheet(book, PLACE_SHEET, PLACE_HEADERS)
    bento = ensure_sheet(book, BENTO_SHEET, BENTO_HEADERS)
    vendor = ensure_sheet(book, VENDOR_SHEET, VENDOR_HEADERS)
    writing = ensure_sheet(book, WRITING_SHEET, WRITING_HEADERS)
    place_header = place.row_values(1) or PLACE_HEADERS
    bento_header = bento.row_values(1) or BENTO_HEADERS
    vendor_header = vendor.row_values(1) or VENDOR_HEADERS
    writing_header = writing.row_values(1) or WRITING_HEADERS
    print("既存の名前を照合しています...")
    return {
        "place": place,
        "bento": bento,
        "vendor": vendor,
        "writing": writing,
        "place_header": place_header,
        "bento_header": bento_header,
        "vendor_header": vendor_header,
        "writing_header": writing_header,
        "place_names": existing_names(place, ("ロケ地名",), 0),
        "bento_names": existing_names(bento, ("店舗名",), 0),
        "vendor_names": existing_names(vendor, ("項目・業者名", "業者名", "項目"), 1),
        "writing_keys": existing_writing_keys(writing),
        "place_origins": existing_origins(place, ("情報元",)),
        "bento_origins": existing_origins(bento, ("提供元",)),
        "vendor_origins": existing_origins(vendor, ("ソース",)),
        "writing_origins": existing_origins(writing, ("情報元",)),
        "csv_names": csv_names(),
    }


def publish(
    book,
    cache: dict,
    locations,
    dressing,
    bento,
    notes,
    writing,
    dry_run: bool,
) -> tuple[bool, str]:
    place_rows = locations + dressing
    sheet_locations = []
    for row in place_rows:
        name_token = norm(row["ロケ地名"])
        if not name_token or name_token in cache["place_names"]:
            continue
        sheet_locations.append(row)

    sheet_bento = [row for row in bento if norm(row["店舗名"]) not in cache["bento_names"]]
    sheet_notes = [row for row in notes if norm(row["項目・業者名"]) not in cache["vendor_names"]]
    sheet_writing = []
    for row in writing:
        key = norm(f"{row.get('用途・シーン','')}|{row.get('件名例','')}|{row.get('情報元','')}")
        origin_scene = norm(f"{row.get('情報元','')}|{row.get('用途・シーン','')}")
        if key in cache["writing_keys"] or origin_scene in cache["writing_keys"]:
            continue
        sheet_writing.append(row)

    if dry_run:
        print(
            f"  確認のみ: ロケ地{len(sheet_locations)}件 / 弁当{len(sheet_bento)}件"
            f" / 注意{len(sheet_notes)}件 / 文章{len(sheet_writing)}件"
        )
        return True, ""
    csv_count = 0
    try:
        csv_count = append_location_csv(place_rows, cache["csv_names"])
        if sheet_locations:
            pairs = named_rows(
                cache["place_header"],
                [aligned_row(cache["place_header"], place_pairs(row)) for row in sheet_locations],
                ("ロケ地名",),
            )
            if pairs:
                append_rows(cache["place"], pairs)
                cache["place_names"].update(norm(row["ロケ地名"]) for row in sheet_locations)
                cache["place_origins"].update(norm(row.get("情報元", "")) for row in sheet_locations if row.get("情報元"))
        if sheet_bento:
            pairs = named_rows(
                cache["bento_header"],
                [aligned_row(cache["bento_header"], bento_pairs(row)) for row in sheet_bento],
                ("店舗名",),
            )
            if pairs:
                append_rows(cache["bento"], pairs)
                cache["bento_names"].update(norm(row["店舗名"]) for row in sheet_bento)
        if sheet_notes:
            pairs = named_rows(
                cache["vendor_header"],
                [aligned_row(cache["vendor_header"], vendor_pairs(row)) for row in sheet_notes],
                ("項目・業者名", "業者名", "項目"),
            )
            if pairs:
                append_rows(cache["vendor"], pairs)
                cache["vendor_names"].update(norm(row["項目・業者名"]) for row in sheet_notes)
        if sheet_writing:
            pairs = named_rows(
                cache["writing_header"],
                [aligned_row(cache["writing_header"], writing_pairs(row)) for row in sheet_writing],
                ("用途・シーン", "用途", "シーン"),
            )
            if pairs:
                append_rows(cache["writing"], pairs)
                for row in sheet_writing:
                    cache["writing_keys"].add(
                        norm(f"{row.get('用途・シーン','')}|{row.get('件名例','')}|{row.get('情報元','')}")
                    )
                    cache["writing_keys"].add(norm(f"{row.get('情報元','')}|{row.get('用途・シーン','')}"))
                    if row.get("情報元"):
                        cache["writing_origins"].add(norm(row["情報元"]))
    except Exception as exc:
        print(f"  台帳への追記に失敗しました（{short_error(exc)}）")
        return False, short_error(exc)
    if csv_count or sheet_locations or sheet_bento or sheet_notes or sheet_writing:
        print(
            f"  登録: ロケ地{len(sheet_locations)}件 / 弁当{len(sheet_bento)}件"
            f" / 注意{len(sheet_notes)}件 / 文章{len(sheet_writing)}件（辞書へ{csv_count}件）"
        )
    return True, ""


def make_client():
    from google import genai

    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        print("GEMINI_API_KEY が .env にありません。設定してから再実行してください。")
        return None
    return genai.Client(api_key=key)


def run(query: str, dry_run: bool, limit: int) -> int:
    print(f"検索条件: {query}")
    service = gmail_service()
    if service is None:
        return 1
    log = load_processed()
    try:
        message_ids = list_message_ids(service, query, limit)
    except Exception as exc:
        print(f"メール一覧の取得に失敗しました（{short_error(exc)}）")
        return 1
    if not message_ids:
        print("条件に合うメールがありませんでした。")
        return 0
    pending_ids = [mid for mid in message_ids if (log["ids"].get(mid) or {}).get("status") != "done"]
    print(f"取得 {len(message_ids)} 件のうち、未処理 {len(pending_ids)} 件を解析します。")
    if not pending_ids:
        print("新しいメールはありません。取り込み済みです。")
        return 0

    client = None
    book = None
    cache = None
    total = len(pending_ids)
    for index, message_id in enumerate(pending_ids, start=1):
        print(f"[{index}/{total}] メール {message_id} を解析中...", flush=True)
        try:
            message = fetch_message(service, message_id)
            text, origin, meta = build_mail_corpus(service, message)
        except Exception as exc:
            print(f"[{index}/{total}] メール取得失敗（{short_error(exc)}）")
            continue
        print(f"  件名: {meta.get('subject', '')} / 添付{meta.get('attachments', 0)}件")
        if not clean(text, 40):
            print("  抽出できる本文が無いためスキップ")
            if not dry_run:
                remember(log, message_id, "done", {}, meta)
            continue
        if client is None:
            client = make_client()
            if client is None:
                return 1
        try:
            locations, dressing, bento, notes, writing, truncated = analyze_text(client, text, origin)
        except Exception as exc:
            print(f"  解析失敗（{short_error(exc)}）")
            if is_gemini_auth_error(exc):
                print("Gemini認証エラーのため、ここで中断します。キー更新後に同じコマンドを再実行してください。")
                return 1
            continue
        print(
            f"  抽出: ロケ地{len(locations)}件 / 控室{len(dressing)}件 / 弁当{len(bento)}件"
            f" / 注意{len(notes)}件 / 文章{len(writing)}件"
        )
        if truncated:
            print("  長いメールのため、先頭部分だけを見ました。")
        counts = {
            "locations": len(locations),
            "dressing": len(dressing),
            "bento": len(bento),
            "notes": len(notes),
            "writing": len(writing),
        }
        if not any(counts.values()):
            if not dry_run:
                remember(log, message_id, "done", counts, meta)
            continue
        if book is None and not dry_run:
            book = open_book()
            if book is None:
                return 1
            try:
                cache = prepare_sheets(book)
            except Exception as exc:
                print(f"既存データの照合に失敗しました（{short_error(exc)}）")
                return 1
        if dry_run:
            cache = cache or {
                "place_names": set(),
                "bento_names": set(),
                "vendor_names": set(),
                "writing_keys": set(),
                "place_origins": set(),
                "writing_origins": set(),
                "csv_names": set(),
                "place_header": PLACE_HEADERS,
                "bento_header": BENTO_HEADERS,
                "vendor_header": VENDOR_HEADERS,
                "writing_header": WRITING_HEADERS,
                "place": None,
                "bento": None,
                "vendor": None,
                "writing": None,
            }
        ok, _message = publish(book, cache, locations, dressing, bento, notes, writing, dry_run)
        if dry_run:
            continue
        if ok:
            remember(log, message_id, "done", counts, meta)
        else:
            remember(log, message_id, "pending", counts, meta)
    print("Gmail取り込みが終わりました。")
    return 0


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="Gmail本文と添付から現場データ・文章ナレッジを取り込む")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="過去何日分を対象にするか（既定180）")
    parser.add_argument("--query", default="", help="Gmail検索クエリを直接指定")
    parser.add_argument("--limit", type=int, default=0, help="処理するメール数の上限")
    parser.add_argument("--dry-run", action="store_true", help="登録せず、抽出結果の件数だけを見る")
    args = parser.parse_args()
    query = args.query.strip() or default_query(args.days)
    return run(query, args.dry_run, max(args.limit, 0))


if __name__ == "__main__":
    raise SystemExit(main())
