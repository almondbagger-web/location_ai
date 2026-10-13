import csv
import time
import urllib.parse
from collections import OrderedDict

from generate_karte import MODEL, DailyQuotaExceeded, client

INPUT_CSV = "location_karte.csv"
OUTPUT_CSV = "summary_karte.csv"
FIELDNAMES = ["ファイル名", "ロケ地フォルダ名", "撮影日時", "GoogleマップURL", "制作部ロケ地カルテ", "ファイルパス"]
SUMMARY_FIELDS = ["ロケ地名", "写真枚数", "GoogleマップURL", "ロケ地総合カルテ"]


def map_search_url(folder_name):
    query = urllib.parse.quote(folder_name)
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def fill_map_urls(rows):
    filled = 0
    for row in rows:
        if row.get("GoogleマップURL", "").strip():
            continue
        folder_name = row.get("ロケ地フォルダ名", "").strip()
        if not folder_name:
            continue
        row["GoogleマップURL"] = map_search_url(folder_name)
        filled += 1
    return filled


def load_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_rows(path, rows):
    with open(path, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def group_by_folder(rows):
    groups = OrderedDict()
    for row in rows:
        folder_name = row.get("ロケ地フォルダ名", "").strip() or "(フォルダなし)"
        groups.setdefault(folder_name, []).append(row)
    return groups


def summarize_location(folder_name, kartes):
    joined = "\n\n".join(
        f"--- 写真{index} ---\n{karte.strip()}"
        for index, karte in enumerate(kartes, start=1)
        if karte.strip()
    )
    prompt = f"""
あなたは日本の映像業界（映画・ドラマ・CM）のロケーションマネージャーです。
同じロケ地「{folder_name}」の写真カルテが{len(kartes)}件あります。重複を整理し、現場スタッフが使う総合カルテに要約してください。

出力はこの3見出しだけにしてください。前置きや締めの文は不要です。
1. 【撮影できるシチュエーション一覧】写真から読み取れるシーン・雰囲気を、重複を除いて箇条書き
2. 【搬入導線・駐車条件】機材搬入、EV/階段、横付け、駐車、電源、雨天待機の見立てを箇条書き
3. 【制作上の注意点・特記事項】近隣、音、安全、施設ルール、撮影条件の注意を箇条書き

各項目は具体的に、合わせて読みやすい分量にしてください。

個別カルテ:
{joined}
"""
    last_error = None
    for attempt in range(6):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e:
            last_error = e
            message = str(e)
            if "PerDay" in message:
                raise DailyQuotaExceeded(message) from e
            transient = any(token in message for token in ("503", "429", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
            if not transient or attempt == 5:
                return f"AI解析エラー: {e}"
            wait = min(60, 2 ** attempt * 3)
            print(f"  一時エラーのため {wait}秒後に再試行 ({attempt + 1}/5)")
            time.sleep(wait)
    return f"AI解析エラー: {last_error}"


def notion_cell(value):
    text = "" if value is None else str(value)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def write_summary(path, records):
    with open(path, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=SUMMARY_FIELDS,
            quoting=csv.QUOTE_ALL,
            lineterminator="\n",
        )
        writer.writeheader()
        for record in records:
            writer.writerow({key: notion_cell(record.get(key, "")) for key in SUMMARY_FIELDS})


def export_notion_csv():
    with open(OUTPUT_CSV, encoding="utf-8-sig", newline="") as f:
        records = list(csv.DictReader(f))
    write_summary(OUTPUT_CSV, records)
    with open(OUTPUT_CSV, encoding="utf-8-sig", newline="") as f:
        raw_lines = f.read().splitlines()
        f.seek(0)
        parsed = list(csv.DictReader(f))
    print(f"物理行数: {len(raw_lines)}（ヘッダー含む）")
    print(f"読み戻し行数: {len(parsed)}")
    for row in parsed:
        print(f"- {row['ロケ地名']} / {row['写真枚数']}枚")
    print(f"上書き完了: {OUTPUT_CSV}")


def main():
    rows = load_rows(INPUT_CSV)
    filled = fill_map_urls(rows)
    save_rows(INPUT_CSV, rows)
    print(f"GoogleマップURLを {filled} 件補完しました: {INPUT_CSV}")

    groups = group_by_folder(rows)
    print(f"{len(groups)} 件のロケ地を要約します...\n")

    summaries = []
    try:
        for index, (folder_name, group_rows) in enumerate(groups.items(), start=1):
            print(f"[{index}/{len(groups)}] 要約中: {folder_name} ({len(group_rows)}枚)")
            kartes = [row.get("制作部ロケ地カルテ", "") for row in group_rows]
            summary = summarize_location(folder_name, kartes)
            summaries.append({
                "ロケ地名": folder_name,
                "写真枚数": len(group_rows),
                "GoogleマップURL": map_search_url(folder_name),
                "ロケ地総合カルテ": summary,
            })
    except DailyQuotaExceeded as e:
        write_summary(OUTPUT_CSV, summaries)
        print(f"\n1日のAPI利用上限に達したため中断しました。ここまでの結果は {OUTPUT_CSV} に保存済みです。")
        print(e)
        return

    write_summary(OUTPUT_CSV, summaries)
    print(f"\n完了！出力ファイル: {OUTPUT_CSV}")


if __name__ == "__main__":
    export_notion_csv()
