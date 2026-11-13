"""LINEトーク履歴と現場フォルダ名から、製作部向けの現場ナレッジを返す。

個人的な私語は捨て、ロケ・許可・弁当・控室・車両など現場語だけを拾う。
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IMPORT_DIR = ROOT / "import_projects"

DAY_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}")
MSG_RE = re.compile(r"^(\d{1,2}:\d{2})\t([^\t]+)\t(.*)$")
FIELD_RE = re.compile(
    r"ロケ|撮影|香盤|弁当|ケータリング|仕出し|許可|警察|道路使用|占用|"
    r"控室|ベース|支度|スタジオ|集合|搬入|搬出|車両|ロケバス|ジェネ|"
    r"近隣|挨拶|養生|トイレ|電源|待機|クランクイン|クランクアップ|"
    r"フィルムコミッション|FC|ロケハン|現場"
)
PRIVATE_RE = re.compile(
    r"保育園|登園|学校|成績|ポケカ|パラセーリング|溶連菌|ママの許可|"
    r"❤️|結婚|離婚|家族旅行"
)
SKIP_SENDERS = {"システム"}


def clean(value: str, limit: int = 240) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = re.sub(r"\(emoji\)|\(facepalm\)|\(にこにこ\)|\(運転\)", "", text)
    text = re.sub(r"\s+", " ", text).strip(" 　\"'")
    return text[:limit]


def find_line_exports() -> list[Path]:
    found: list[Path] = []
    for path in ROOT.rglob("*.txt"):
        name = path.name
        if name.startswith("[LINE]") or "トーク" in name or ("LINE" in name.upper() and "トーク" in name):
            found.append(path)
        elif path.parent == IMPORT_DIR or "import_projects" in path.parts:
            try:
                head = path.read_text(encoding="utf-8", errors="ignore")[:80]
            except OSError:
                continue
            if head.startswith("[LINE]") or "トーク履歴" in head:
                found.append(path)
    # 優先度: import_projects 直下の LINE エクスポート
    found.sort(key=lambda item: (0 if item.parent == IMPORT_DIR else 1, str(item)))
    # 重複除去
    unique = []
    seen = set()
    for path in found:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def parse_line_file(path: Path, limit: int = 400) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    rows: list[dict[str, str]] = []
    day = ""
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if DAY_RE.match(line):
            day = line.strip()
            continue
        match = MSG_RE.match(line)
        if not match:
            continue
        _time, sender, body = match.groups()
        body = clean(body, 400)
        if not body or sender in SKIP_SENDERS:
            continue
        if PRIVATE_RE.search(body):
            continue
        if not FIELD_RE.search(body):
            continue
        # 短すぎる相槌は捨てる
        if len(body) < 10:
            continue
        rows.append(
            {
                "day": day,
                "sender": clean(sender, 40),
                "body": body,
                "source": path.name,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def project_folder_cues(limit: int = 80) -> list[dict[str, str]]:
    """ロケハン写真フォルダ名などから現場メモを拾う。"""
    if not IMPORT_DIR.is_dir():
        return []
    rows: list[dict[str, str]] = []
    seen = set()
    for path in IMPORT_DIR.rglob("*"):
        if not path.is_dir():
            continue
        name = clean(path.name, 80)
        if len(name) < 3:
            continue
        if name.startswith(".") or name in {"ロケハン写真", "とりあえず", "クレジット", "photos"}:
            continue
        # 作品直下の場所フォルダや LINE_ALBUM 親フォルダを優先
        useful = bool(
            re.search(
                r"カフェ|オフィス|スタジオ|橋|公園|ホテル|マンション|ビル|店|学校|病院|港|駅|道|モール|一軒家|戸建",
                name,
            )
        )
        if not useful and "LINE_ALBUM" not in name:
            # 親がロケハン写真なら採用
            if "ロケハン" not in str(path.parent):
                continue
        token = re.sub(r"\s+", "", name)
        if token in seen:
            continue
        seen.add(token)
        work = ""
        try:
            rel = path.relative_to(IMPORT_DIR)
            if rel.parts:
                work = clean(rel.parts[0], 40)
        except ValueError:
            work = ""
        rows.append(
            {
                "day": "",
                "sender": "現場フォルダ",
                "body": clean(f"{name}（作品フォルダ: {work}）" if work else name, 160),
                "source": f"import_projects/{work}" if work else "import_projects",
            }
        )
        if len(rows) >= limit:
            break
    return rows


@lru_cache(maxsize=1)
def load_field_snippets() -> tuple[dict[str, str], ...]:
    snippets: list[dict[str, str]] = []
    for path in find_line_exports()[:8]:
        snippets.extend(parse_line_file(path, limit=250))
    snippets.extend(project_folder_cues(120))
    return tuple(snippets)


def cue_lines(query: str, limit: int = 8) -> list[str]:
    raw = query or ""
    terms = re.findall(r"[一-龥ァ-ヶA-Za-z0-9]{2,16}", raw)
    stop = {"教えて", "ください", "お願い", "現場", "撮影", "作って", "書いて"}
    terms = [term for term in terms if term not in stop][:8]
    rows = list(load_field_snippets())
    if not rows:
        return []
    ranked = []
    for row in rows:
        blob = f"{row.get('body','')} {row.get('source','')}"
        score = sum(2 if term in blob else 0 for term in terms)
        if FIELD_RE.search(raw) and FIELD_RE.search(blob):
            score += 1
        if score <= 0 and not terms:
            score = 1
        if score <= 0:
            continue
        ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    lines = []
    seen = set()
    for _score, row in ranked:
        body = row.get("body") or ""
        key = re.sub(r"\s+", "", body)[:48]
        if not body or key in seen:
            continue
        seen.add(key)
        src = clean(row.get("source") or "LINE/現場メモ", 40)
        lines.append(f"- {body}（情報元: {src}）")
        if len(lines) >= limit:
            break
    return lines
