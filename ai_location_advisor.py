import json
import os
import re
import sqlite3
import textwrap
from pathlib import Path

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from janome.tokenizer import Tokenizer

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "locations_master.db"
ENV_PATH = BASE_DIR / ".env"
SEARCH_COLUMNS = ("location_name", "address", "scenes_category", "detail_text")
CANDIDATE_LIMIT = 15
FETCH_LIMIT = 400
NOTION_VERSION = "2022-06-28"
PREFECTURES = (
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
)
SCENE_TAG_WORDS = (
    "ガラス張り", "高層", "オフィス", "ビル", "病院", "学校", "大学", "高校",
    "カフェ", "屋上", "神社", "寺", "公園", "駅", "橋", "ホール", "スタジオ",
    "マンション", "住宅", "ホテル", "美術館", "博物館", "教会", "工場",
    "劇場", "ロビー", "会議室", "商談", "階段", "校舎",
)

STOPWORDS = {
    "こと", "もの", "ため", "よう", "ところ", "あたり", "以内", "近く", "付近",
    "できる", "ください", "お願い", "撮影", "ロケ", "ロケ地", "シーン", "設定",
    "感じ", "雰囲気", "場所", "施設", "候補", "都内", "都心", "車で", "分以内",
}
EXPANSIONS = {
    "都内": "東京都",
    "都心": "東京都",
    "東京": "東京都",
}

tokenizer = Tokenizer()
load_dotenv(ENV_PATH)


def connect():
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def like_pattern(keyword):
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def extract_keywords(text):
    keywords = []
    for token in tokenizer.tokenize(text):
        pos = token.part_of_speech.split(",")
        if pos[0] != "名詞" or pos[1] in {"非自立", "代名詞", "数", "接尾"}:
            continue
        surface = token.surface.strip()
        if len(surface) < 2 or surface in STOPWORDS:
            continue
        keywords.append(surface)
    for word, extra in EXPANSIONS.items():
        if word in text:
            keywords.append(extra)
    return list(dict.fromkeys(keywords))


def search_candidates(conn, keywords):
    clauses = []
    params = []
    for keyword in keywords:
        pattern = like_pattern(keyword)
        for column in SEARCH_COLUMNS:
            clauses.append(f"{column} LIKE ? ESCAPE '\\'")
            params.append(pattern)
    query = f"""
        SELECT location_name, address, scenes_category, works_title, detail_text, map_url, source_url
        FROM locations
        WHERE {" OR ".join(clauses)}
        LIMIT {FETCH_LIMIT}
    """
    rows = conn.execute(query, params).fetchall()
    ranked = []
    for row in rows:
        fields = {
            "location_name": row[0] or "",
            "address": row[1] or "",
            "scenes_category": row[2] or "",
            "works_title": row[3] or "",
            "detail_text": row[4] or "",
            "map_url": row[5] or "",
            "source_url": row[6] or "",
        }
        score = 0
        for keyword in keywords:
            if keyword in fields["location_name"]:
                score += 5
            if keyword in fields["address"]:
                score += 4
            if keyword in fields["scenes_category"]:
                score += 3
            if keyword in fields["detail_text"]:
                score += 2
        if score:
            fields["score"] = score
            ranked.append(fields)
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:CANDIDATE_LIMIT]


def candidate_brief(candidates):
    blocks = []
    for index, item in enumerate(candidates, start=1):
        detail = re.sub(r"\s+", " ", item["detail_text"])[:360]
        blocks.append(
            "\n".join([
                f"[{index}] ロケ地名: {item['location_name']}",
                f"住所: {item['address']}",
                f"シーン・設定: {item['scenes_category']}",
                f"作品名: {item['works_title']}",
                f"GoogleマップURL: {item['map_url']}",
                f"情報元URL: {item['source_url']}",
                f"詳細: {detail}",
            ])
        )
    return "\n\n".join(blocks)


def advise(request_text, candidates):
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が .env にありません。")
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip() or "gemini-3.1-flash-lite"
    prompt = f"""
あなたは日本の映像制作部のロケ地チーフです。
要望に対し、渡した候補だけから3〜5件を選んで推薦してください。候補にない施設は出さないでください。
GoogleマップURLと情報元URLは候補の値をそのまま写してください。

要望:
{request_text}

候補:
{candidate_brief(candidates)}

次のJSONだけを返してください。
{{
  "recommendations": [
    {{
      "location_name": "ロケ地名",
      "address": "住所",
      "map_url": "GoogleマップURL",
      "source_url": "情報元URL",
      "reason": "制作チーフ視点の提案理由",
      "points": "ロケハン時の推奨ポイント"
    }}
  ]
}}
"""
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    recommendations = parse_recommendations(response.text)
    attach_source_fields(recommendations, candidates)
    return recommendations


def attach_source_fields(recommendations, candidates):
    for item in recommendations:
        match = None
        for candidate in candidates:
            same_name = candidate["location_name"] == item["location_name"]
            same_map = item["map_url"] and candidate["map_url"] == item["map_url"]
            if same_name and same_map:
                match = candidate
                break
        if match is None:
            for candidate in candidates:
                if candidate["location_name"] == item["location_name"]:
                    match = candidate
                    break
        item["scenes_category"] = (match or {}).get("scenes_category", item.get("scenes_category", ""))
        item["works_title"] = (match or {}).get("works_title", item.get("works_title", ""))
        if match and not item["address"]:
            item["address"] = match["address"]
        if match and not item["map_url"]:
            item["map_url"] = match["map_url"]
        if match and not item["source_url"]:
            item["source_url"] = match["source_url"]


def parse_recommendations(text):
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    data = json.loads(raw)
    recommendations = data.get("recommendations", [])
    cleaned = []
    for item in recommendations[:5]:
        cleaned.append({
            "location_name": str(item.get("location_name", "")).strip(),
            "address": str(item.get("address", "")).strip(),
            "map_url": str(item.get("map_url", "")).strip(),
            "source_url": str(item.get("source_url", "")).strip(),
            "reason": str(item.get("reason", "")).strip(),
            "points": str(item.get("points", "")).strip(),
            "scenes_category": str(item.get("scenes_category", "")).strip(),
            "works_title": str(item.get("works_title", "")).strip(),
        })
    return cleaned


def show_report(recommendations):
    print(f"\n制作部向け推薦 {len(recommendations)}件\n")
    for index, item in enumerate(recommendations, start=1):
        print(f"{index}. {item['location_name']}")
        print(f"   所在地: {item['address'] or '不明'}")
        print(textwrap.fill(f"提案理由: {item['reason']}", width=88, subsequent_indent="   "))
        print(textwrap.fill(f"ロケハン: {item['points']}", width=88, subsequent_indent="   "))
        print(f"   地図: {item['map_url'] or 'なし'}")
        print()


def karte_text(item):
    return "\n".join([
        f"提案理由: {item['reason']}",
        f"ロケハン時の推奨ポイント: {item['points']}",
        f"所在地: {item['address']}",
        f"情報元: {item['source_url']}",
    ])


def prefecture_from(address):
    text = address or ""
    for name in PREFECTURES:
        if name in text:
            return name
    return ""


def scene_tags(item):
    tags = []
    raw = " ".join([
        item.get("scenes_category", ""),
        item.get("reason", ""),
        item.get("points", ""),
    ])
    for part in re.split(r"[/／、,，|]+", item.get("scenes_category", "")):
        part = re.sub(r"[\(（][^)）]*[\)）]", "", part).strip()
        if 1 < len(part) <= 12 and "。" not in part:
            tags.append(part)
    for word in SCENE_TAG_WORDS:
        if word in raw:
            tags.append(word)
    unique = []
    for tag in tags:
        tag = tag.replace(",", " ").strip()
        if tag and tag not in unique:
            unique.append(tag[:100])
    return unique[:8]


def notion_properties(item):
    properties = {
        "ロケ地名": {"title": [{"type": "text", "text": {"content": item["location_name"][:2000]}}]},
        "ロケ地総合カルテ": {"rich_text": rich_text_chunks(karte_text(item))},
        "ステータス": {"status": {"name": "候補"}},
    }
    if item.get("map_url", "").startswith("http"):
        properties["GoogleマップURL"] = {"url": item["map_url"]}
    prefecture = prefecture_from(item.get("address", ""))
    if prefecture:
        properties["都道府県"] = {"select": {"name": prefecture}}
    tags = scene_tags(item)
    if tags:
        properties["シーン分類"] = {"multi_select": [{"name": tag} for tag in tags]}
    works = (item.get("works_title") or "").strip()
    if works:
        properties["撮影実績作品"] = {"rich_text": rich_text_chunks(works)}
    return properties


def property_name_from_error(message):
    patterns = (
        r"(.+?) is not a property that exists",
        r"body\.properties\.([^\s\.]+)",
        r"property[\"']?\s*[\"'](.+?)[\"']",
    )
    for pattern in patterns:
        match = re.search(pattern, message or "")
        if match:
            return match.group(1).strip().strip('"').strip("'")
    return ""


def post_notion_page(database_id, headers, properties):
    return requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json={"parent": {"database_id": database_id}, "properties": properties},
        timeout=30,
    )


def rich_text_chunks(text):
    chunks = []
    remaining = text or ""
    while remaining:
        chunks.append({"type": "text", "text": {"content": remaining[:2000]}})
        remaining = remaining[2000:]
    return chunks or [{"type": "text", "text": {"content": ""}}]


def register_notion(item):
    try:
        import streamlit as st

        api_key = str(st.secrets.get("NOTION_API_KEY", "") or "").strip()
        database_id = str(st.secrets.get("NOTION_DATABASE_ID", "") or "").strip()
    except Exception:
        api_key = ""
        database_id = ""
    if not api_key or not database_id:
        raise RuntimeError("NOTION_API_KEY または NOTION_DATABASE_ID が st.secrets にありません。")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    properties = notion_properties(item)
    try:
        schema = requests.get(
            f"https://api.notion.com/v1/databases/{database_id}",
            headers=headers,
            timeout=30,
        )
        if schema.status_code < 400:
            existing = set(schema.json().get("properties", {}))
            properties = {name: value for name, value in properties.items() if name in existing}
    except requests.RequestException:
        pass
    if "ロケ地名" not in properties:
        properties["ロケ地名"] = notion_properties(item)["ロケ地名"]
    skipped = []
    response = None
    for _ in range(len(properties) + 1):
        response = post_notion_page(database_id, headers, properties)
        if response.status_code < 400:
            if skipped:
                print("  未対応のため省略: " + "、".join(skipped))
            return response.json().get("url", "")
        message = ""
        try:
            message = response.json().get("message", "")
        except ValueError:
            message = response.text
        missing = property_name_from_error(message)
        if missing in properties and missing != "ロケ地名":
            properties.pop(missing)
            skipped.append(missing)
            continue
        if "ステータス" in properties and "status" in message.lower():
            properties.pop("ステータス")
            skipped.append("ステータス")
            continue
        break
    detail = response.text[:400] if response is not None else ""
    raise RuntimeError(f"Notion登録に失敗しました ({response.status_code if response else 'no response'}): {detail}")


def choose_and_register(recommendations):
    if not recommendations:
        return
    print("Notionへ登録する番号を入力してください。例: 1 / 1 3 / all")
    print("登録しない場合は Enter です。")
    try:
        choice = input("登録> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not choice:
        return
    if choice == "all":
        selected = recommendations
    else:
        indexes = []
        for part in re.split(r"[\s,、]+", choice):
            if part.isdigit():
                indexes.append(int(part))
        selected = [recommendations[index - 1] for index in indexes if 1 <= index <= len(recommendations)]
    if not selected:
        print("番号が見つかりませんでした。")
        return
    for item in selected:
        try:
            url = register_notion(item)
            print(f"登録しました: {item['location_name']} {url}")
        except Exception as error:
            print(f"登録できませんでした: {item['location_name']} ({error})")


def research_locations(request_text):
    keywords = extract_keywords(request_text)
    if not keywords:
        raise RuntimeError("検索に使える名詞が見つかりませんでした。施設やエリアの言葉を入れてください。")
    conn = connect()
    try:
        candidates = search_candidates(conn, keywords)
    finally:
        conn.close()
    if not candidates:
        raise RuntimeError("候補が見つかりませんでした。")
    recommendations = advise(request_text, candidates)
    if not recommendations:
        raise RuntimeError("推薦を作成できませんでした。")
    return recommendations


def main():
    if not DB_PATH.exists():
        print(f"データベースが見つかりません: {DB_PATH}")
        return
    conn = connect()
    print("制作部 AIロケ地提案（データベースは読み取り専用）")
    print("要望を入力してください。終了は exit または q です。")
    while True:
        try:
            request_text = input("\n要望> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if request_text.lower() in {"exit", "q", "quit"}:
            break
        if not request_text:
            continue
        keywords = extract_keywords(request_text)
        if not keywords:
            print("検索に使える名詞が見つかりませんでした。施設やエリアの言葉を入れてください。")
            continue
        print("キーワード: " + " / ".join(keywords))
        candidates = search_candidates(conn, keywords)
        if not candidates:
            print("候補が見つかりませんでした。")
            continue
        print(f"データベース候補: {len(candidates)}件。推薦を作成しています...")
        try:
            recommendations = advise(request_text, candidates)
        except Exception as error:
            print(f"推薦を作成できませんでした: {error}")
            continue
        show_report(recommendations)
        choose_and_register(recommendations)
    conn.close()


if __name__ == "__main__":
    main()
