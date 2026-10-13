import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "locations_master.db"
SEARCH_COLUMNS = (
    "location_name",
    "address",
    "scenes_category",
    "detail_text",
    "works_title",
)
DISPLAY_LIMIT = 20


def connect():
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def like_pattern(keyword):
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search(conn, keywords):
    clauses = []
    params = []
    for keyword in keywords:
        pattern = like_pattern(keyword)
        clauses.append(
            "(" + " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in SEARCH_COLUMNS) + ")"
        )
        params.extend([pattern] * len(SEARCH_COLUMNS))
    where = " AND ".join(clauses)
    total = conn.execute(f"SELECT COUNT(*) FROM locations WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        f"""
        SELECT location_name, address, scenes_category, works_title, map_url
        FROM locations
        WHERE {where}
        ORDER BY id DESC
        LIMIT {DISPLAY_LIMIT}
        """,
        params,
    ).fetchall()
    return total, rows


def show(keywords, total, rows):
    label = " ".join(keywords)
    print(f"\n「{label}」: {total}件見つかりました", end="")
    if total > len(rows):
        print(f"（上位{len(rows)}件を表示）")
    else:
        print()
    if not rows:
        return
    for index, (name, address, scene, work, map_url) in enumerate(rows, start=1):
        print(f"\n{index}. {name or '（名称なし）'}")
        print(f"   所在地: {address or '不明'}")
        print(f"   シーン/設定: {scene or '不明'}")
        print(f"   作品名: {work or '不明'}")
        print(f"   地図: {map_url or 'なし'}")
    print()


def main():
    if not DB_PATH.exists():
        print(f"データベースが見つかりません: {DB_PATH}")
        return
    conn = connect()
    print("ロケ地検索（読み取り専用）")
    print("キーワードをスペース区切りで入力してください。終了は exit または q です。")
    while True:
        try:
            query = input("\n検索> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if query.lower() in {"exit", "q", "quit"}:
            break
        if not query:
            continue
        keywords = query.split()
        total, rows = search(conn, keywords)
        show(keywords, total, rows)
    conn.close()


if __name__ == "__main__":
    main()
