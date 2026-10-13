"""制作部の表から、施設名・住所・シーンだけを locations に入れる。

電話番号、担当者名、口座、自宅住所は保存しない。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import openpyxl

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "locations_master.db"

FILES = [
    "/Users/user/Desktop/番組別/DTM/とりあえず/行動表/「DTM」制作部行動表.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/精算/精算/「SLM」精算表1回目.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/精算/精算/「SLM」精算表２回目.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/精算/精算/「SLM」精算表３回目.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/精算/精算/「SLM」精算表１１回目.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/精算/精算/「SLM」精算表１２回目.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/精算/精算/「SLM」精算表１３回目.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/予算/「アイシー」制作部予算書①②③話　１１月１５日バージョン　.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/予算/「アイシー」制作部予算書①②③話　１2月3日バージョン　.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/予算/「アイシー」制作部予算書④⑤話　１１月１５日バージョン　.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/予算/「アイシー」制作部予算書④⑤話　１２月１５日バージョン　.xlsx",
    "/Users/user/Desktop/番組別/憧れの作家/「憧れの作家は人間じゃない」制作部予算書.xlsx",
    "/Users/user/Desktop/番組別/DTM/ロケハン写真/1決定/料亭/神楽坂　おいしんぼ　料亭/『DTM』撮影場所リスト.xlsx",
    "/Users/user/Desktop/とりあえず/『末端』撮影場所　弓田加筆.xlsx",
    "/Users/user/Desktop/番組別/アイシー/ロケハンメニュー/【仮】1030フジテレビ佐藤組「アイシー」メインロケハンメニュー.xlsx",
    "/Users/user/Desktop/番組別/アイシー/ロケハンメニュー/【決】1030フジテレビ佐藤組「アイシー」メインロケハンメニュー.xlsx",
    "/Users/user/Desktop/0424JMS物件リスト.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/予算/0711「ロマンティックキラー」制作部予算書.xlsx",
    "/Users/user/Desktop/番組別/ロマンティックキラー/0803ロマキラロケ地物件リスト(2).xlsx",
    "/Users/user/Desktop/番組別/ロマンティックキラー/ゴミ/0803ロマキラロケ地物件リスト(2).xlsx",
    "/Users/user/Desktop/番組別/撮影関係/予算/0829「ロマンティックキラー」制作部予算書.xlsx",
    "/Users/user/Desktop/番組別/ロマンティックキラー/スケジュール/ロマキラ/日々スケ/0830「ロマンティックキラー」制作部予算書.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/予算/0830「ロマンティックキラー」制作部予算書.xlsx",
    "/Users/user/Desktop/番組別/DTM/とりあえず/1114 _DTM_クレジット.xlsx",
    "/Users/user/Desktop/番組別/DTM/1225 _DTM_クレジット.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/予算/Aパターン　0712「ロマンティックキラー」制作部予算書.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/予算/Bパターン　0712「ロマンティックキラー」制作部予算書.xlsx",
    "/Users/user/Desktop/番組別/DTM/とりあえず/DTM　クレジット.xlsx",
    "/Users/user/Desktop/番組別/DTM/ロケハンスケジュール/JMS物件表0324.xlsx",
    "/Users/user/Desktop/番組別/DTM/ゴミ/DTM_精算書類/K2_DTM仮払精算フォーム_250120.xlsx",
    "/Users/user/Desktop/番組別/DTM/ゴミ/DTM_精算書類/２月１０日までK2_DTM仮払精算フォーム_250120.xlsx",
    "/Users/user/Desktop/番組別/DTM/ゴミ/DTM_精算書類/K2_DTM仮払精算フォーム_弓田（２月１０日〜２月２８日）.xlsx",
    "/Users/user/Desktop/ロケ地表.xlsx",
    "/Users/user/Desktop/個人諸々/AI企画/ロケ地表.xlsx",
    "/Users/user/Desktop/番組別/ロマンティックキラー/ゴミ/ロマキラロケ地調査リスト.xlsx",
    "/Users/user/Desktop/番組別/ロマンティックキラー/ロマキラ請求書リスト.xlsx",
    "/Users/user/Desktop/番組別/ロマンティックキラー/仮払い/仮払い表.xlsx",
    "/Users/user/Desktop/番組別/ハッピーリスト/外山組_精算_弓田２月分.xlsx",
    "/Users/user/Desktop/番組別/ハッピーリスト/外山組_精算フォーマット_v1_260130.xlsx",
    "/Users/user/Desktop/番組別/ハッピーリスト/弓田２月分　外山組_精算フォーマット_v1_260130.xlsx",
    "/Users/user/Desktop/再雇用警察官　暫定のロケ地一覧.xlsx",
    "/Users/user/Desktop/個人諸々/AI企画/再雇用警察官　暫定のロケ地一覧.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/予算/請求書リスト.xlsx",
    "/Users/user/Desktop/番組別/DTM/とりあえず/総スケ0709.xlsx",
    "/Users/user/Desktop/番組別/DTM/ゴミ/スケジュール/総スケ0709.xlsx",
    "/Users/user/Desktop/番組別/憧れの作家/憧れの作家　物件表.xlsx",
    "/Users/user/Desktop/番組別/憧れの作家/憧れの作家　物件表1219.xlsx",
    "/Users/user/Desktop/番組別/憧れの作家/憧れの作家　物件表1225.xlsx",
    "/Users/user/Desktop/番組別/憧れの作家/憧れの作家割り振り.xlsx",
    "/Users/user/Desktop/番組別/撮影関係/物件リスト/物件まとめ1116更新.xlsx",
    "/Users/user/Desktop/番組別/DTM/とりあえず/未決定物件　リスト/未決定物件リスト_0831_0３.xlsx",
    "/Users/user/Desktop/番組別/DTM/とりあえず/未決定物件　リスト/未決定物件リスト_0831_02.xlsx",
    "/Users/user/Desktop/番組別/DTM/とりあえず/未決定物件　リスト/未決定物件リスト_0831_02１.xlsx",
]

WORKS = (
    ("ロマンティックキラー", "ロマンティックキラー"),
    ("ロマキラ", "ロマンティックキラー"),
    ("憧れの作家", "憧れの作家は人間じゃない"),
    ("再雇用警察官", "再雇用警察官"),
    ("アイシー", "アイシー"),
    ("末端", "末端"),
    ("DTM", "DTM"),
    ("SLM", "SLM"),
    ("JMS", "JMS"),
    ("ハッピーリスト", "ハッピーリスト"),
)
NAME_HEADERS = ("ロケ地名称", "ロケ場所", "撮影場所", "物件名", "施設名", "ロケ地")
SCENE_HEADERS = ("場面・設定・撮影予定日", "シーン名", "場面", "シーン", "設定", "柱")
NOTE_HEADERS = ("撮影条件", "スケジュール条件", "条件", "備考")
SKIP_NAME = {"確認中", "未定", "なし", "決定", "レギュラー", "ng", "ok", "-", "―", "物件費", "シーン"}
PRIVATE_RE = re.compile(r"自宅|個人宅|実家|の家|中古物件|戸建|邸(?!ビル)")
PHONE_RE = re.compile(r"(?:0\d{1,4}|\d{2,4})[-‐－]\d{1,4}[-‐－]\d{3,4}|0\d{9,11}")
PERSON_RE = re.compile(r"[一-龯ァ-ヶA-Za-z]{1,8}さん")
SKIP_LABEL = ("担当", "連絡先", "氏名", "電話", "口座", "振込", "金額", "単価", "料金", "使用料", "支払い", "精算")


def clean(value) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).replace("\u3000", " ").strip()
    text = PHONE_RE.sub("", text)
    text = PERSON_RE.sub("", text)
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"\s+", " ", text).strip(" /・")


def work_title(path: Path) -> str:
    blob = str(path)
    for key, title in WORKS:
        if key in blob:
            return title
    return ""


def header_map(row: tuple) -> dict | None:
    found: dict[str, int] = {}
    for index, cell in enumerate(row):
        label = clean(cell).replace(" ", "")
        if not label or any(word in label for word in SKIP_LABEL):
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


def cell(row: tuple, index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return clean(row[index])


def acceptable(name: str, address: str) -> bool:
    if len(name) < 2 or len(name) > 80:
        return False
    if name.casefold() in SKIP_NAME or name.isdigit():
        return False
    if PRIVATE_RE.search(name) or PRIVATE_RE.search(address):
        return False
    if "@" in name or "氏名" in name:
        return False
    return True


def resolve_files() -> list[Path]:
    by_name: dict[str, Path] = {}
    for root in (Path("/Users/user/Desktop"),):
        for path in root.rglob("*.xlsx"):
            if path.name.startswith("~$") or "個人情報" in str(path):
                continue
            by_name.setdefault(path.name, path)
    paths = []
    for raw in FILES:
        path = Path(raw)
        if path.name.startswith("~$") or "個人情報" in str(path):
            continue
        if not path.exists():
            path = by_name.get(path.name, path)
        if path not in paths:
            paths.append(path)
    return paths


def main():
    conn = sqlite3.connect(DB_PATH)
    inserted = skipped_private = missing = 0
    seen = set()
    paths = resolve_files()
    for path in paths:
        if not path.exists():
            missing += 1
            continue
        try:
            book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        except Exception:
            missing += 1
            continue
        work = work_title(path)
        source = f"production:{path.name}"[:180]
        try:
            for sheet in book.worksheets:
                columns = None
                for row in sheet.iter_rows(values_only=True):
                    if not row or not any(row):
                        continue
                    mapped = header_map(row)
                    if mapped:
                        columns = mapped
                        continue
                    if not columns:
                        continue
                    name = cell(row, columns.get("name"))
                    address = cell(row, columns.get("address"))
                    scene = cell(row, columns.get("scene"))[:80]
                    note = cell(row, columns.get("note"))[:120]
                    if not acceptable(name, address):
                        if name and (PRIVATE_RE.search(name) or PRIVATE_RE.search(address)):
                            skipped_private += 1
                        continue
                    key = (name, address)
                    if key in seen:
                        continue
                    exists = conn.execute(
                        "SELECT id FROM locations WHERE location_name = ? AND IFNULL(address, '') = ? LIMIT 1",
                        (name, address),
                    ).fetchone()
                    if exists:
                        seen.add(key)
                        continue
                    detail = " / ".join(part for part in (scene, note) if part)[:180]
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO locations (
                            site_name, location_name, address, scenes_category, works_title,
                            detail_text, map_url, source_url, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, '', ?, datetime('now'))
                        """,
                        ("制作部ロケ地台帳", name, address, scene, work, detail, source),
                    )
                    seen.add(key)
                    inserted += 1
        finally:
            book.close()
    conn.commit()
    conn.close()
    print(f"inserted {inserted} private_skipped {skipped_private} unreadable_or_missing {missing}")


if __name__ == "__main__":
    main()
