import csv
import sqlite3

DB_PATH = "locations_master.db"
CSV_PATH = "notion_all_locations.csv"
COLUMNS = [
    ("id", "id"),
    ("site_name", "サイト"),
    ("location_name", "ロケ地名"),
    ("address", "住所"),
    ("scenes_category", "シーン・設定・施設カテゴリ"),
    ("works_title", "関連作品情報"),
    ("detail_text", "詳細"),
    ("map_url", "GoogleマップURL"),
    ("source_url", "情報元URL"),
    ("created_at", "取得日時"),
]


def notion_cell(value):
    text = "" if value is None else str(value)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " / ")


def export():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = conn.execute(
        """
        SELECT id, site_name, location_name, address, scenes_category,
               works_title, detail_text, map_url, source_url, created_at
        FROM locations
        ORDER BY id
        """
    ).fetchall()
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writerow([label for _column, label in COLUMNS])
        for row in rows:
            writer.writerow([notion_cell(value) for value in row])
    print(f"{CSV_PATH} に {len(rows)} 件出力しました。")


if __name__ == "__main__":
    export()
