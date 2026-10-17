"""bulk_locations_master.csv を起動時に読み、ロケ地質問の参考実績を返す。

読み込みに失敗しても空のまま返す。呼び出し側のスプレッドシート検索は止めない。
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "bulk_locations_master.csv"
PRIVATE_RE = re.compile(r"自宅|個人宅|実家|号室|表札")
DATE_TAIL_RE = re.compile(r"\s*\d{4}\s*年.*$")
QUOTE_RE = re.compile(r"[「『]([^」』]{2,24})[」』]")
PLACE_RE = re.compile(
    r"[一-龥A-Za-z0-9]{2,16}(?:学校|病院|神社|寺|公園|駅|橋|大学|高校|ホール|スタジオ|カフェ|ホテル|旅館|ビル|タワー|資料館|書院|城)"
)
TOKEN_RE = re.compile(r"[一-龥ァ-ヶA-Za-z0-9]{2,12}")
STOP = {
    "ロケ", "ロケ地", "場所", "教えて", "ください", "お願い", "ドラマ", "今期", "候補",
    "撮影", "シーン", "附近", "あたり", "できる", "どこ", "探し", "探して", "ほしい",
    "欲しい", "です", "ます", "したい", "知りたい", "おすすめ", "ある", "ない",
    "施設", "撮影場所", "ロケハン", "過去", "実績", "撮影できる", "教えてください",
}
AREAS = (
    "北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島", "茨城", "栃木", "群馬",
    "埼玉", "千葉", "東京", "神奈川", "新潟", "富山", "石川", "福井", "山梨", "長野",
    "岐阜", "静岡", "愛知", "三重", "滋賀", "京都", "大阪", "兵庫", "奈良", "和歌山",
    "鳥取", "島根", "岡山", "広島", "山口", "徳島", "香川", "愛媛", "高知", "福岡",
    "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島", "沖縄", "横浜", "川崎", "神戸",
    "札幌", "仙台", "名古屋",
)
NON_TOKYO = tuple(name for name in AREAS if name not in {"東京"})

_records: list[tuple[str, str, str, str, str]] = []
_loaded = False
_failed = False


def ensure_loaded() -> None:
    global _loaded, _failed
    if _loaded or _failed:
        return
    try:
        if not CSV_PATH.is_file():
            _failed = True
            return
        rows: list[tuple[str, str, str, str, str]] = []
        with CSV_PATH.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                name = str(raw.get("ロケ地名") or "").strip()
                address = str(raw.get("住所") or "").strip()
                area = str(raw.get("エリア") or "").strip()
                work = DATE_TAIL_RE.sub("", str(raw.get("作品名") or "")).strip()
                scene = str(raw.get("シーン説明") or "").strip()
                if len(name) < 2:
                    continue
                blob = " ".join((name, address, area, work, scene))
                if PRIVATE_RE.search(blob):
                    continue
                rows.append((name, address or area, work[:80], scene[:80], blob))
        _records.extend(rows)
        _loaded = True
    except Exception:
        _records.clear()
        _failed = True


def query_terms(query: str) -> list[str]:
    raw = query or ""
    found: list[str] = []
    for term in QUOTE_RE.findall(raw) + PLACE_RE.findall(raw) + TOKEN_RE.findall(raw):
        term = term.strip()
        if len(term) < 2 or term in STOP or term in found:
            continue
        found.append(term)
    kept = [term for term in found if not any(term != other and term in other for other in found)]
    kept.sort(key=len, reverse=True)
    return kept[:4]


def search(query: str, limit: int = 5) -> list[dict[str, str]]:
    try:
        ensure_loaded()
        if not _records:
            return []
        terms = query_terms(query)
        if not terms:
            return []
        outside = any(name in (query or "") for name in NON_TOKYO)

        def rank(required: list[str]) -> list[tuple[int, int]]:
            found: list[tuple[int, int]] = []
            for index, row in enumerate(_records):
                blob = row[4]
                if any(term not in blob for term in required):
                    continue
                score = 0
                for term in required:
                    score += 4 if term in row[0] else 1
                    if term in row[2]:
                        score += 2
                if not outside and "東京" in blob:
                    score += 2
                found.append((score, index))
            found.sort(key=lambda item: item[0], reverse=True)
            return found

        scored = rank(terms)
        if not scored and len(terms) > 1:
            scored = rank(terms[:1])
        picked: list[dict[str, str]] = []
        seen: set[str] = set()
        for _score, index in scored:
            name, address, work, scene, _blob = _records[index]
            key = f"{name}|{address}"
            if key in seen:
                continue
            seen.add(key)
            picked.append({"name": name, "address": address, "work": work, "scene": scene})
            if len(picked) >= limit:
                break
        return picked
    except Exception:
        return []


def reference_block(query: str) -> str:
    try:
        rows = search(query, 5)
        if not rows:
            return ""
        lines = ["【参考過去ロケ実績】"]
        for row in rows:
            bits = [row["name"]]
            if row["address"]:
                bits.append(row["address"])
            if row["work"]:
                bits.append(row["work"])
            if row["scene"]:
                bits.append(row["scene"])
            lines.append("- " + " / ".join(bits))
        return "\n".join(lines)
    except Exception:
        return ""


try:
    ensure_loaded()
except Exception:
    _failed = True
