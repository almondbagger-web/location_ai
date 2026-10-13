import csv
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "locations_master.db"
CURRENT_YEAR = 2026
RECENT_FROM = CURRENT_YEAR - 19  # 2007年〜2026年

EXCLUDE_WORDS = [
    "閉鎖",
    "解体",
    "閉店",
    "廃業",
    "取り壊し",
    "現存せず",
    "跡地",
    "消滅",
    "建替",
]
TOKYO_WEST_WARDS = ("港区", "渋谷区", "新宿区", "世田谷区", "目黒区", "品川区", "大田区")
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})年")

OUTPUTS = [
    ("mymap_1_tokyo_west.csv", "tokyo_west", 2000),
    ("mymap_2_tokyo_others.csv", "tokyo_others", 2000),
    ("mymap_3_kanagawa.csv", "kanagawa", 2000),
    ("mymap_4_saitama.csv", "saitama", 1500),
    ("mymap_5_chiba.csv", "chiba", 1500),
    ("mymap_6_ibaraki.csv", "ibaraki", 200),
    ("mymap_7_gunma.csv", "gunma", 200),
    ("mymap_8_yamanashi.csv", "yamanashi", 200),
    ("mymap_9_shizuoka.csv", "shizuoka", 200),
]
COLUMNS = ["ロケ地名", "住所 / 所在地", "シーン・設定", "主な作品名", "詳細テキスト", "情報元URL"]


def connect():
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def has_exclude_word(row):
    blob = " ".join(row[key] or "" for key in ("location_name", "address", "scenes_category", "detail_text"))
    return any(word in blob for word in EXCLUDE_WORDS)


def years_in(row):
    blob = f"{row['works_title'] or ''} {row['detail_text'] or ''}"
    return [int(year) for year in YEAR_RE.findall(blob)]


def has_recent_year(years):
    return any(RECENT_FROM <= year <= CURRENT_YEAR for year in years)


def newest_year(years):
    recent = [year for year in years if RECENT_FROM <= year <= CURRENT_YEAR]
    return max(recent) if recent else 0


def region_of(address):
    text = address or ""
    if "東京都" in text or any(ward in text for ward in TOKYO_WEST_WARDS):
        if "東京都" in text or not any(pref in text for pref in ("神奈川県", "埼玉県", "千葉県", "茨城県", "群馬県", "山梨県", "静岡県")):
            if any(ward in text for ward in TOKYO_WEST_WARDS):
                return "tokyo_west"
            if "東京都" in text:
                return "tokyo_others"
    if "神奈川県" in text:
        return "kanagawa"
    if "埼玉県" in text:
        return "saitama"
    if "千葉県" in text:
        return "chiba"
    if "茨城県" in text:
        return "ibaraki"
    if "群馬県" in text:
        return "gunma"
    if "山梨県" in text:
        return "yamanashi"
    if "静岡県" in text:
        return "shizuoka"
    return None


def cell(value):
    text = "" if value is None else str(value)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " / ")


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "ロケ地名": cell(row["location_name"]),
                "住所 / 所在地": cell(row["address"]),
                "シーン・設定": cell(row["scenes_category"]),
                "主な作品名": cell(row["works_title"]),
                "詳細テキスト": cell(row["detail_text"]),
                "情報元URL": cell(row["source_url"]),
            })


def main():
    conn = connect()
    conn.row_factory = sqlite3.Row
    source = conn.execute(
        """
        SELECT id, location_name, address, scenes_category, works_title, detail_text, source_url
        FROM locations
        """
    ).fetchall()

    excluded_closed = 0
    excluded_old = 0
    outside_region = 0
    grouped = {key: [] for _name, key, _limit in OUTPUTS}

    for record in source:
        row = dict(record)
        if has_exclude_word(row):
            excluded_closed += 1
            continue
        years = years_in(row)
        if not has_recent_year(years):
            excluded_old += 1
            continue
        region = region_of(row["address"])
        if region is None:
            outside_region += 1
            continue
        row["newest_year"] = newest_year(years)
        grouped[region].append(row)

    print(f"読み込み件数: {len(source)}")
    print(f"除外（閉鎖・解体など）: {excluded_closed}")
    print(f"除外（2006年以前、または2007年以降の年がない）: {excluded_old}")
    print(f"対象外（1都7県以外）: {outside_region}")

    truncated = 0
    for filename, key, limit in OUTPUTS:
        rows = sorted(grouped[key], key=lambda item: (item["newest_year"], item["id"]), reverse=True)
        chosen = rows[:limit]
        truncated += max(0, len(rows) - len(chosen))
        write_csv(Path(__file__).resolve().parent / filename, chosen)
        print(f"{filename}: {len(chosen)}件（候補 {len(rows)}件、上限 {limit}件）")
    print(f"上限超過で未出力: {truncated}")


if __name__ == "__main__":
    main()
