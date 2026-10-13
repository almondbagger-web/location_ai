"""日程文・連絡先・短すぎる汎用ラベルを locations_master.db から外す。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from location_hygiene import is_contaminated_record

DB_PATH = Path(__file__).resolve().parent / "locations_master.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    location_ids = []
    learned_ids = []
    try:
        for row in conn.execute("SELECT id, location_name, detail_text, site_name FROM locations"):
            if is_contaminated_record(row[1], row[2] or "", row[3] or ""):
                location_ids.append(row[0])
        for row in conn.execute("SELECT id, title, description, source_url FROM auto_learned_locations"):
            site = "番組別アーカイブ" if str(row[3] or "").startswith("local-archive") else ""
            if is_contaminated_record(row[1], row[2] or "", site):
                learned_ids.append(row[0])
        for start in range(0, len(location_ids), 400):
            chunk = location_ids[start : start + 400]
            conn.execute(f"DELETE FROM locations WHERE id IN ({','.join('?' for _ in chunk)})", chunk)
        for start in range(0, len(learned_ids), 400):
            chunk = learned_ids[start : start + 400]
            conn.execute(
                f"DELETE FROM auto_learned_locations WHERE id IN ({','.join('?' for _ in chunk)})",
                chunk,
            )
        conn.commit()
    finally:
        conn.close()
    print(f"削除: locations {len(location_ids)} / auto_learned_locations {len(learned_ids)}")


if __name__ == "__main__":
    main()
