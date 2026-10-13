"""道路使用の中日、牽引の可否、公園撮影許可を社内台帳から引く。"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_PATH = BASE / "permit_knowledge.json"
WEAK_AREAS = {"中央", "東", "西", "南", "北", "新", "本", "緑"}
WEAK_TOWNS = {"中央", "本町", "緑町", "旭町", "東町", "西町", "南町", "北町", "新町", "栄町", "泉町", "大字", "全域", "市内"}
LOCATION_EXTRA = re.compile(r"学校|ビル|病院|神社|寺|スタジオ|ロケ地|商店街|オフィス|海岸|駅前|撮影できる|設定")
PLACE_SEARCH_RE = re.compile(r"撮影できる|撮れる場所|のロケ地|ロケ地は|ロケ地を|ロケ候補|設定の施設|設定の場所|設定で")
PROCEDURE_RE = re.compile(r"申請手順|申請の仕方|手続き|取り方|届出")


@lru_cache(maxsize=1)
def load_permit_data() -> dict:
    if not DATA_PATH.exists():
        return {"stations": [], "parks": []}
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def is_place_search(text: str) -> bool:
    raw = text or ""
    if PROCEDURE_RE.search(raw) and re.search(r"道路|警察|公園|許可|占用|申請", raw):
        return False
    return bool(PLACE_SEARCH_RE.search(raw))


def is_permit_query(text: str) -> bool:
    raw = text or ""
    if is_place_search(raw):
        return False
    if PROCEDURE_RE.search(raw) and re.search(r"道路|警察|公園|許可|占用|申請", raw):
        return True
    if re.search(r"道路使用|中日|牽引|けん引|荷台乗車", raw):
        return True
    if "警察署" in raw and re.search(r"許可|申請|中日|日数|可否", raw):
        return True
    return bool("公園" in raw and re.search(r"許可|占用|申請|夜間|料金", raw))


def wants_location_too(text: str) -> bool:
    return bool(LOCATION_EXTRA.search(text or "")) or is_place_search(text or "")


def _stem(area: str) -> str:
    return re.sub(r"(区|市|町|村)$", "", area or "")


def _lead_phrase(station: dict) -> str:
    older = station.get("lead_days_2012") or ""
    newer = station.get("lead_days_2018") or ""
    if older and newer and older != newer:
        return f"中日は2012.7.20の表で{older}、2018年12月の署一覧で{newer}"
    if older and newer:
        return f"中日{older}（2012.7.20の表と2018年12月の署一覧で同じ）"
    if older:
        return f"中日{older}（2012.7.20の表）"
    if newer:
        return f"中日{newer}（2018年12月の署一覧）"
    return "中日の記載なし"


def _match_stations(query: str, stations: list[dict]) -> tuple[list[dict], bool]:
    exact = [row for row in stations if row["name"] and row["name"] in query]
    if exact and "署" in query:
        return exact, True
    areas = []
    for row in stations:
        area = row.get("area") or ""
        stem = _stem(area)
        if area and area in query:
            areas.append(area)
        elif len(stem) >= 2 and stem not in WEAK_AREAS and stem in query:
            areas.append(area)
    if areas:
        chosen = {area for area in areas}
        return [row for row in stations if row.get("area") in chosen], True
    named = []
    for row in stations:
        stem = row["name"].replace("警察署", "")
        if len(stem) >= 2 and stem in query:
            named.append(row)
    if named:
        return named, True
    hits = []
    for row in stations:
        for token in re.split(r"[、,\s]+", row.get("jurisdiction") or ""):
            token = token.strip("：・()（）")
            if len(token) < 2 or token in WEAK_TOWNS:
                continue
            if token in query:
                hits.append(row)
                break
    if hits:
        return hits, True
    asked = bool(re.search(r"区|市|町|村|警察|署", query))
    return [], asked


def _match_parks(query: str, parks: list[dict]) -> tuple[list[dict], bool]:
    hits = []
    for park in parks:
        area = park.get("area") or ""
        stem = _stem(area)
        if area and area in query:
            hits.append(park)
        elif len(stem) >= 2 and stem not in WEAK_AREAS and stem in query:
            hits.append(park)
    if hits:
        return hits, True
    return [], bool(re.search(r"区|市|町|村", query))


def _station_detail(station: dict) -> str:
    area = station.get("area") or "区域の記載なし"
    lines = [f"{station['name']}（{area}）", f"- {_lead_phrase(station)}"]
    if station.get("period"):
        lines.append(f"- 使用できる期間: {station['period']}")
    flags = []
    if station.get("still"):
        flags.append(f"止め撮り{station['still']}")
    if station.get("drive"):
        flags.append(f"走行撮影{station['drive']}")
    if station.get("tow"):
        flags.append(f"牽引{station['tow']}")
    if flags:
        lines.append("- " + " / ".join(flags) + "（2018年12月の署一覧）")
    if station.get("phone"):
        lines.append(f"- 電話: {station['phone']}")
    if station.get("address"):
        lines.append(f"- 署の所在: {station['address']}")
    if station.get("note"):
        lines.append(f"- 表の注意: {station['note']}")
    if station.get("pdf_note"):
        lines.append(f"- 署一覧のメモ: {station['pdf_note']}")
    if station.get("jurisdiction"):
        lines.append(f"- 管轄の町名: {station['jurisdiction'][:180]}")
    return "\n".join(lines)


def _station_compact(station: dict) -> str:
    area = station.get("area") or ""
    tow = station.get("tow") or "記載なし"
    phone = f" / {station['phone']}" if station.get("phone") else ""
    return f"- {station['name']}（{area}）牽引{tow} / {_lead_phrase(station)}{phone}"


def _park_detail(park: dict) -> str:
    night = {"〇": "可", "○": "可", "×": "不可"}.get(park.get("night") or "", park.get("night") or "記載なし")
    allowed = "可" if park.get("allowed") in ("〇", "○") else (park.get("allowed") or "記載なし")
    lines = [
        f"{park['area']}の公園撮影",
        f"- 撮影: {allowed}",
        f"- 窓口: {park.get('contact') or '記載なし'}",
        f"- 曜日: {park.get('days') or '記載なし'}",
        f"- 時間: {park.get('hours') or '記載なし'}",
        f"- 夜間: {night}",
    ]
    if park.get("time_limit"):
        lines.append(f"- 時間制限: {park['time_limit']}")
    if park.get("people"):
        lines.append(f"- 人数: {park['people']}")
    if park.get("ng"):
        lines.append(f"- NG: {park['ng']}")
    if park.get("fee"):
        lines.append(f"- 料金: {park['fee']}")
    if park.get("note"):
        lines.append(f"- メモ: {park['note']}")
    return "\n".join(lines)


def _park_compact(park: dict) -> str:
    night = {"〇": "夜間可", "○": "夜間可", "×": "夜間不可"}.get(park.get("night") or "", f"夜間{park.get('night') or '記載なし'}")
    fee = park.get("fee") or "料金記載なし"
    return f"- {park['area']}: {night} / {park.get('hours') or '時間記載なし'} / {fee}"


def permit_answer(query: str) -> str:
    data = load_permit_data()
    stations = data.get("stations") or []
    parks = data.get("parks") or []
    if not stations and not parks:
        return "許可台帳がまだ入っていません。"
    tow_intent = bool(re.search(r"牽引|けん引|荷台", query))
    park_intent = bool("公園" in query and re.search(r"許可|占用|申請|夜間|料金|撮影", query))
    road_intent = bool(re.search(r"道路使用|中日|警察", query)) or not (tow_intent or park_intent)
    sections = []
    matched_stations, place_asked = _match_stations(query, stations)
    if road_intent or tow_intent:
        if matched_stations:
            body = "\n\n".join(_station_detail(row) for row in matched_stations)
            sections.append("道路使用・牽引（社内台帳）\n" + body)
        elif place_asked:
            sections.append("その区・市・警察署は、手元の東京都の道路使用台帳にありません。区名か警察署名でもう一度指定してください。")
        elif tow_intent:
            allowed = [row for row in stations if row.get("tow") == "可"]
            consult = [row for row in stations if row.get("tow") == "応相談"]
            lines = ["牽引が記録上とれる警察署（2018年12月の署一覧）", "可"]
            lines.extend(_station_compact(row) for row in allowed)
            lines.append("応相談")
            lines.extend(_station_compact(row) for row in consult)
            lines.append("ここに無い署は、同じ一覧では牽引不可か、欄が空です。南大沢警察署は牽引欄が空です。")
            sections.append("\n".join(lines))
        elif re.search(r"一覧|全部|どこ", query):
            lines = ["東京都の警察署ごとの中日（2012.7.20の表。違う場合は2018年12月も併記）"]
            lines.extend(_station_compact(row) for row in stations)
            sections.append("\n".join(lines))
        else:
            sections.append("道路使用の中日は区名か警察署名を指定すると、その署の日数・牽引の可否・注意を出します。")
    if park_intent:
        matched_parks, park_place = _match_parks(query, parks)
        if matched_parks:
            sections.append("公園撮影（2022.6.22の一覧）\n" + "\n\n".join(_park_detail(row) for row in matched_parks))
        elif park_place:
            sections.append("その市区町村は、2022.6.22の公園撮影許可一覧にありません。")
        else:
            lines = ["東京都の公園撮影許可（2022.6.22の一覧）。区名を指定すると窓口とNGまで出します。"]
            lines.extend(_park_compact(row) for row in parks)
            sections.append("\n".join(lines))
    sections.append("日数も牽引も公園条件も、表を作った時点の記録です。申請の前に、その警察署か公園窓口へもう一度確認してください。")
    return "\n\n".join(sections)
