import os
import csv
import time
from PIL import Image, ExifTags
from google import genai
from google.genai import types

GEMINI_API_KEY = "AQ.Ab8RN6J7im3Oond-epDTQ26Yd8psvQ-apMfJ11Qa4qIGaZUdcQ"
INPUT_FOLDER = "./photos"
OUTPUT_CSV = "location_karte.csv"
MODEL = "gemini-3.1-flash-lite"

class DailyQuotaExceeded(Exception):
    pass

client = genai.Client(api_key=GEMINI_API_KEY)

def get_decimal_from_dms(dms, ref):
    degrees = float(dms[0])
    minutes = float(dms[1])
    seconds = float(dms[2])
    decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)
    if ref in ['S', 'W']:
        decimal = -decimal
    return decimal

def extract_exif(image_path):
    try:
        img = Image.open(image_path)
        exif_raw = img._getexif()
        if not exif_raw:
            return "不明", ""

        exif_data = {ExifTags.TAGS.get(k, k): v for k, v in exif_raw.items()}
        date_taken = exif_data.get('DateTimeOriginal', exif_data.get('DateTime', '不明'))

        gps_info = exif_data.get('GPSInfo')
        map_url = ""
        if gps_info:
            gps_tags = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_info.items()}
            lat_dms = gps_tags.get('GPSLatitude')
            lat_ref = gps_tags.get('GPSLatitudeRef')
            lon_dms = gps_tags.get('GPSLongitude')
            lon_ref = gps_tags.get('GPSLongitudeRef')

            if lat_dms and lat_ref and lon_dms and lon_ref:
                lat = get_decimal_from_dms(lat_dms, lat_ref)
                lon = get_decimal_from_dms(lon_dms, lon_ref)
                map_url = f"https://www.google.com/maps?q={lat},{lon}"

        return date_taken, map_url
    except Exception as e:
        return "エラー", ""

def analyze_photo_with_ai(image_path, mime_type):
    prompt = """
あなたは日本の映像業界（映画・ドラマ・CM）の超優秀な「制作担当・ロケーションマネージャー」です。
提供されたロケハン写真をプロの制作部目線で精査し、以下の項目を簡潔・具体的に回答してください。

1. 【ロケ地タイプ】（例: 昭和風古民家、近代オフィス、廃工場、屋上、雑居ビル階段など）
2. 【シチュエーション・雰囲気】（例: 日当たり良好、暗めサスペンス向き、生活感あり、抜けが良い）
3. 【制作部チェック（搬入・インフラ推測）】
   - 階段かEVか、搬入導線（機材車が横付けできそうか）
   - 電源の確保や雨天時の待機・雨宿りスペースの見立て
4. 【想定される注意事項・懸念点】（例: 近隣住宅近接で音出し注意、日没が早そう、道幅狭小）

※回答は各項目1〜2行で、現場スタッフがパッと見てわかるように箇条書きにしてください。
"""
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    last_error = None
    for attempt in range(6):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=[
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type=mime_type
                    ),
                    prompt
                ]
            )
            return response.text.strip()
        except DailyQuotaExceeded:
            raise
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

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
FIELDNAMES = ["ファイル名", "ロケ地フォルダ名", "撮影日時", "GoogleマップURL", "制作部ロケ地カルテ", "ファイルパス"]

def collect_photos(root):
    photos = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        rel_dir = os.path.relpath(dirpath, root)
        folder_name = "" if rel_dir == "." else rel_dir
        for filename in sorted(filenames):
            ext = os.path.splitext(filename)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            photos.append({
                "folder_name": folder_name,
                "filename": filename,
                "path": os.path.join(dirpath, filename),
                "mime_type": MIME_TYPES[ext],
            })
    return photos

def load_completed(path):
    completed = {}
    if not os.path.exists(path):
        return completed
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            karte = row.get("制作部ロケ地カルテ", "")
            file_path = row.get("ファイルパス", "")
            if file_path and karte and not karte.startswith("AI解析エラー"):
                completed[file_path] = {key: row.get(key, "") for key in FIELDNAMES}
    return completed

def main():
    if not os.path.exists(INPUT_FOLDER):
        print(f"フォルダが見つかりません: {INPUT_FOLDER}")
        return

    photos = collect_photos(INPUT_FOLDER)
    if not photos:
        print("指定フォルダに対象の写真が見つかりませんでした。")
        return

    completed = load_completed(OUTPUT_CSV)
    pending = [photo for photo in photos if os.path.abspath(photo["path"]) not in completed]
    print(f"合計 {len(photos)} 枚（解析済み {len(completed)} 枚、残り {len(pending)} 枚）を処理します...\n")

    with open(OUTPUT_CSV, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in completed.values():
            writer.writerow(row)
        f.flush()

        try:
            for idx, photo in enumerate(pending, start=1):
                image_path = photo["path"]
                label = f"{photo['folder_name']}/{photo['filename']}" if photo["folder_name"] else photo["filename"]
                print(f"[{idx}/{len(pending)}] 処理中: {label}")

                date_taken, map_url = extract_exif(image_path)
                karte = analyze_photo_with_ai(image_path, photo["mime_type"])

                writer.writerow({
                    "ファイル名": photo["filename"],
                    "ロケ地フォルダ名": photo["folder_name"],
                    "撮影日時": date_taken,
                    "GoogleマップURL": map_url,
                    "制作部ロケ地カルテ": karte,
                    "ファイルパス": os.path.abspath(image_path),
                })
                f.flush()
        except DailyQuotaExceeded as e:
            print(f"\n1日のAPI利用上限に達したため中断しました。ここまでの結果は {OUTPUT_CSV} に保存済みです。")
            print(e)
            return

    print(f"\n完了！出力ファイル: {OUTPUT_CSV}")

if __name__ == "__main__":
    main()
