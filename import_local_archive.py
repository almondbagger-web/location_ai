"""~/Desktop/番組別 からロケ地と制作ノウハウを locations_master.db に入れる。

電話番号、メール、連絡網・香盤の個人欄、個人宅は保存しない。
同じ施設名と住所が既にあれば、ノウハウと写真パスを追記して更新する。

例:
  python3 import_local_archive.py
  python3 import_local_archive.py --root ~/Desktop/番組別
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import openpyxl

from location_hygiene import is_bad_facility_name

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "locations_master.db"
DEFAULT_ROOT = Path.home() / "Desktop" / "番組別"
SITE_NAME = "番組別アーカイブ"
DOC_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".pdf", ".docx", ".txt", ".csv"}
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png"}
NAME_HEADERS = ("ロケ地名称", "ロケ場所", "撮影場所", "物件名", "施設名", "ロケ地")
SCENE_HEADERS = ("場面・設定・撮影予定日", "シーン名", "場面", "シーン", "設定", "柱")
NOTE_HEADERS = ("撮影条件", "スケジュール条件", "条件", "備考", "注意")
SKIP_HEADER = ("担当", "連絡先", "氏名", "電話", "tel", "mail", "メール", "口座", "振込", "香盤", "名簿")
SKIP_FILE = re.compile(r"香盤|連絡網|名簿|クレジット|口座|マイナンバー|給与|源泉|身分証|表札|個人情報")
SKIP_DIR = re.compile(r"個人情報|連絡網|名簿|クレジット|香盤|口座|マイナンバー")
PRIVATE_RE = re.compile(r"自宅|個人宅|実家|の家|中古物件|戸建|一軒家|表札|号室|邸(?!ビル)")
PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,4}[-‐－\s]?\d{1,4}[-‐－\s]?\d{3,4}|\+81[-‐－\s]?\d[\d\-‐－\s]{8,})(?!\d)"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
KNOWHOW_RE = re.compile(r"駐車|養生|消えモノ|消モノ|撮影時間|時間制限|搬入|完徹|近隣|騒音|車両|台数|NG|立ち入り|立入")
ADDRESS_RE = re.compile(r"(?:北海道|東京都|京都府|大阪府|.{2,3}県|.{1,6}[市区町村]).{0,40}(?:丁目|番地|番|号|付近)?")
SKIP_NAME = {"確認中", "未定", "なし", "決定", "レギュラー", "ng", "ok", "-", "―", "物件費", "シーン", "場所"}


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def mask(text: str) -> str:
    cleaned = PHONE_RE.sub("", text or "")
    cleaned = EMAIL_RE.sub("", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" /・")


def clean(value) -> str:
    return mask(re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip())


def program_name(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return root.name
    return relative.parts[0] if relative.parts else root.name


def blocked_path(path: Path) -> bool:
    if path.name.startswith("~$") or path.name.startswith("."):
        return True
    parts = [nfc(part) for part in path.parts]
    name = nfc(path.name)
    if any("個人情報" in part for part in parts):
        return True
    if any(SKIP_DIR.search(part) for part in parts):
        return True
    if SKIP_FILE.search(name):
        return True
    if re.search(r"精算|仮払|請求書", name) and not re.search(r"物件|ロケ地|撮影場所", name):
        return True
    return False


def private_place(name: str, address: str) -> bool:
    return bool(PRIVATE_RE.search(name) or PRIVATE_RE.search(address))


def has_knowhow(text: str) -> bool:
    return bool(KNOWHOW_RE.search(text or ""))


class ArchiveImporter:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(db_path)
        self.inserted = 0
        self.updated = 0
        self.knowhow = 0
        self.photos = 0
        self.excluded = 0
        self.programs: set[str] = set()

    def close(self):
        self.conn.commit()
        self.conn.close()

    def upsert(self, work: str, name: str, address: str, scene: str, detail: str, source: str, photo: str = ""):
        name = mask(name)[:120]
        address = mask(address)[:200]
        scene = mask(scene)[:120]
        detail = mask(detail)[:500]
        photo = mask(photo)[:300]
        if not name or name.casefold() in SKIP_NAME or is_bad_facility_name(name):
            self.excluded += 1
            return
        if private_place(name, address) or PHONE_RE.search(name) or EMAIL_RE.search(name):
            self.excluded += 1
            return
        if photo:
            detail = (detail + f" 写真: {photo}").strip()
            self.photos += 1
        knowhow = has_knowhow(detail) or has_knowhow(scene)
        current = self._find(name, address, work)
        if current:
            row_id, old_detail, old_work, old_scene = current
            merged = self._merge(old_detail or "", detail)
            self.conn.execute(
                """
                UPDATE locations
                SET address = CASE WHEN ? != '' THEN ? ELSE address END,
                    scenes_category = CASE WHEN scenes_category IS NULL OR scenes_category = '' THEN ? ELSE scenes_category END,
                    works_title = CASE WHEN works_title IS NULL OR works_title = '' THEN ? ELSE works_title END,
                    detail_text = ?,
                    site_name = CASE WHEN site_name IS NULL OR site_name = '' THEN ? ELSE site_name END
                WHERE id = ?
                """,
                (address, address, scene, work or old_work or "", merged[:800], SITE_NAME, row_id),
            )
            self.updated += 1
        else:
            try:
                self.conn.execute(
                    """
                    INSERT INTO locations (
                        site_name, location_name, address, scenes_category, works_title,
                        detail_text, map_url, source_url, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, '', ?, datetime('now'))
                    """,
                    (SITE_NAME, name, address, scene, work, detail, source[:240]),
                )
                self.inserted += 1
            except sqlite3.IntegrityError:
                self.conn.execute(
                    """
                    UPDATE locations
                    SET detail_text = ?, works_title = CASE WHEN works_title IS NULL OR works_title = '' THEN ? ELSE works_title END
                    WHERE source_url = ? AND location_name = ? AND IFNULL(address, '') = ? AND IFNULL(scenes_category, '') = ?
                    """,
                    (detail, work, source[:240], name, address, scene),
                )
                self.updated += 1
        if knowhow:
            self.knowhow += 1
        self._upsert_learned(work, name, address, scene, detail, source)

    def _find(self, name: str, address: str, work: str):
        if address:
            return self.conn.execute(
                """
                SELECT id, detail_text, works_title, scenes_category
                FROM locations
                WHERE location_name = ? AND replace(IFNULL(address, ''), ' ', '') = replace(?, ' ', '')
                LIMIT 1
                """,
                (name, address),
            ).fetchone()
        return self.conn.execute(
            """
            SELECT id, detail_text, works_title, scenes_category
            FROM locations
            WHERE location_name = ? AND IFNULL(works_title, '') = ?
            LIMIT 1
            """,
            (name, work),
        ).fetchone()

    def _merge(self, old: str, new: str) -> str:
        if not new or new in old:
            return old
        if not old:
            return new
        return f"{old} / {new}"

    def _upsert_learned(self, work: str, name: str, address: str, scene: str, detail: str, source: str):
        description = " / ".join(part for part in (scene, detail) if part)[:500]
        row = self.conn.execute(
            "SELECT id FROM auto_learned_locations WHERE title = ? AND IFNULL(source_url, '') = ? LIMIT 1",
            (name, source[:240]),
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE auto_learned_locations SET work_name = ?, address = ?, description = ? WHERE id = ?",
                (work, address, description, row[0]),
            )
            return
        self.conn.execute(
            """
            INSERT INTO auto_learned_locations (title, work_name, address, description, source_url)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, work, address, description, source[:240]),
        )


def header_map(row: tuple) -> dict | None:
    found: dict[str, int] = {}
    for index, cell in enumerate(row):
        label = clean(cell).replace(" ", "").lower()
        if not label or any(word in label for word in SKIP_HEADER):
            continue
        if any(key in label for key in NAME_HEADERS) and "name" not in found:
            found["name"] = index
        elif label == "場所" and "name" not in found:
            found["name"] = index
        elif "住所" in label and "address" not in found:
            found["address"] = index
        elif any(key in label for key in SCENE_HEADERS) and "scene" not in found:
            found["scene"] = index
        elif any(key in label for key in NOTE_HEADERS) and "note" not in found:
            found["note"] = index
    if "address" in found and "name" not in found and found["address"] > 0:
        found["name"] = found["address"] - 1
    if "name" not in found:
        return None
    return found


def take(row: tuple, index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return clean(row[index])


def rows_from_sheet(sheet) -> list[dict]:
    records = []
    columns = None
    for row in sheet.iter_rows(values_only=True):
        if not row or not any(row):
            continue
        mapped = header_map(tuple(row))
        if mapped:
            columns = mapped
            continue
        if not columns:
            continue
        name = take(tuple(row), columns.get("name"))
        address = take(tuple(row), columns.get("address"))
        scene = take(tuple(row), columns.get("scene"))
        note = take(tuple(row), columns.get("note"))
        if private_place(name, address):
            continue
        records.append({"name": name, "address": address, "scene": scene, "detail": note})
    return records


def read_xlsx(path: Path) -> list[dict]:
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    records = []
    try:
        for sheet in book.worksheets:
            if SKIP_FILE.search(sheet.title or ""):
                continue
            records.extend(rows_from_sheet(sheet))
    finally:
        book.close()
    return records


def read_xls(path: Path) -> list[dict]:
    import xlrd

    book = xlrd.open_workbook(path)
    records = []
    for sheet in book.sheets():
        if SKIP_FILE.search(sheet.name or ""):
            continue
        columns = None
        for index in range(sheet.nrows):
            row = tuple(sheet.cell_value(index, col) for col in range(sheet.ncols))
            mapped = header_map(row)
            if mapped:
                columns = mapped
                continue
            if not columns:
                continue
            name = take(row, columns.get("name"))
            address = take(row, columns.get("address"))
            if private_place(name, address):
                continue
            records.append(
                {
                    "name": name,
                    "address": address,
                    "scene": take(row, columns.get("scene")),
                    "detail": take(row, columns.get("note")),
                }
            )
    return records


def read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    texts = [node.text or "" for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
    return "\n".join(texts)


def read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages[:25]:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def snippets_from_text(text: str, fallback_name: str) -> list[dict]:
    records = []
    masked = mask(text)
    for chunk in re.split(r"\n+", masked):
        chunk = clean(chunk)
        if len(chunk) < 8 or private_place(chunk, ""):
            continue
        address_match = ADDRESS_RE.search(chunk)
        if not address_match and not has_knowhow(chunk):
            continue
        address = clean(address_match.group(0)) if address_match else ""
        if private_place("", address):
            continue
        name = fallback_name
        if address and address in chunk:
            head = clean(chunk.split(address)[0])[:40]
            if 2 <= len(head) <= 40:
                name = head
        records.append({"name": name[:80], "address": address[:120], "scene": "", "detail": chunk[:180]})
        if len(records) >= 8:
            break
    return records


def read_document(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return read_xlsx(path)
    if suffix == ".xls":
        return read_xls(path)
    if suffix == ".csv":
        text = path.read_text(encoding="utf-8", errors="ignore")
        return snippets_from_text(text, path.stem)
    if suffix == ".txt":
        return snippets_from_text(path.read_text(encoding="utf-8", errors="ignore"), path.stem)
    if suffix == ".docx":
        return snippets_from_text(read_docx(path), path.stem)
    if suffix == ".pdf":
        return snippets_from_text(read_pdf(path), path.stem)
    return []


def collect_files(root: Path):
    documents = []
    photo_groups: dict[Path, list[Path]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or blocked_path(path):
            continue
        suffix = path.suffix.lower()
        if suffix in DOC_SUFFIXES:
            documents.append(path)
        elif suffix in PHOTO_SUFFIXES:
            if blocked_path(path.parent):
                continue
            photo_groups.setdefault(path.parent, []).append(path)
    return documents, photo_groups


def main():
    parser = argparse.ArgumentParser(description="番組別フォルダからロケ地と制作ノウハウを取り込む")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="番組フォルダの親")
    parser.add_argument("--limit-docs", type=int, default=0, help="文書の試し取り件数。0 ですべて")
    parser.add_argument("--limit-photos", type=int, default=0, help="写真フォルダの試し取り件数。0 ですべて")
    args = parser.parse_args()
    root = Path(args.root).expanduser()
    if not root.is_dir():
        raise SystemExit(f"フォルダがありません: {root}")
    documents, photo_groups = collect_files(root)
    if args.limit_docs:
        documents = documents[: args.limit_docs]
    photo_items = list(photo_groups.items())
    if args.limit_photos:
        photo_items = photo_items[: args.limit_photos]
    programs = {program_name(root, path) for path in documents}
    programs.update(program_name(root, folder) for folder, _ in photo_items)
    print(f"対象: {root}")
    print(f"番組 {len(programs)} / 文書 {len(documents)} / 写真フォルダ {len(photo_items)}")
    importer = ArchiveImporter(DB_PATH)
    importer.programs = programs
    try:
        for index, path in enumerate(documents, 1):
            work = program_name(root, path)
            source = f"local-archive:{path.relative_to(root)}"
            try:
                records = read_document(path)
            except Exception:
                print(f"  [{index}/{len(documents)}] 読み飛ばし {path.name}")
                continue
            kept = 0
            for record in records:
                before = importer.excluded
                importer.upsert(work, record["name"], record["address"], record["scene"], record["detail"], source)
                if importer.excluded == before:
                    kept += 1
            if index == 1 or index % 25 == 0 or index == len(documents):
                print(f"  文書 {index}/{len(documents)} {work} / {path.name} -> {kept}件")
        for index, (folder, photos) in enumerate(photo_items, 1):
            work = program_name(root, folder)
            place = clean(folder.name)
            if private_place(place, "") or not place or place in {root.name, work} or is_bad_facility_name(place):
                importer.excluded += 1
                continue
            shown = []
            for photo in photos[:5]:
                try:
                    shown.append(str(photo.relative_to(root)))
                except ValueError:
                    shown.append(photo.name)
            detail = f"ロケ地写真 {len(photos)}枚"
            importer.upsert(work, place, "", "", detail, f"local-archive-photo:{folder.relative_to(root)}", " / ".join(shown))
            if index == 1 or index % 100 == 0 or index == len(photo_items):
                print(f"  写真 {index}/{len(photo_items)} {work} / {place} {len(photos)}枚")
    finally:
        importer.close()
    print(
        f"\n完了: 番組 {len(programs)} / ロケ地 新規 {importer.inserted} / 更新 {importer.updated}"
        f" / ノウハウ {importer.knowhow} / 写真記録 {importer.photos} / 個人情報などで除外 {importer.excluded}"
    )


if __name__ == "__main__":
    main()
