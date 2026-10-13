"""施設名に混ざった日程・連絡先・文書見出しを判定する。"""

from __future__ import annotations

import re

STAFF_RE = re.compile(r"弓田|[@＠]\s*山田|山田\s*[さん氏様]|制作部\s*[:：]?\s*山田")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
DATE_RE = re.compile(r"[0-9０-９]{1,4}\s*月|[0-9０-９]{1,2}\s*日")
DOC_RE = re.compile(r"お願い|注意|共有|打合せ|打ち合わせ|スケジュール|決定稿|＜|＞|<|>|〈|〉|様式|申込|年月日|使用日")
IMPORT_ROLE_RE = re.compile(r"@|＠|監督|進行|制作|氏名|TEL|Tel|tel|090")
ROLE_P_RE = re.compile(r"(?:^|[\s　・,、/])[PＰ](?:$|[\s　・,、/])")
HEADING_RE = re.compile(r"のお願い|注意事項|スケジュール|決定稿|打合せ|打ち合わせ|(?:^|[\s　])メモ(?:$|[\s　：:])|^メモ$")
FACILITY_HINT_RE = re.compile(r"店|校|館|寺|神社|ビル|スタジオ|大学|病院|駅|ホール|公園|通り|坂|橋|タワー|ホテル|カフェ|会館|劇場|役所|スタジアム|海岸|ビーチ|温泉|城|宮|院")
OFFICE_WORD_RE = re.compile(
    r"領収|請求|数量|単位|単価|金額|摘要|見積|誓約|契約書|承諾|同意書|登記|本店移転|法人番号|受付番号|"
    r"共有|お願い|注意|決定稿|準備稿|台帳|連絡網|名簿|御中"
)
VERSION_RE = re.compile(r"(?<![A-Za-z])ver\.", re.I)
HONORIFIC_DONO_RE = re.compile(r"(?:^|[\s　])殿(?:$|[\s　:：、。])")
PAGE_SLASH_RE = re.compile(r"1/")
PROSE_RE = re.compile(
    r"ください|いたします|申し上げ|申請書|下記の通り|企画書|要項|発行日|ご請求|お見積|"
    r"スケジュール|取扱|承諾事項|配車|荷下ろし|作品概要|ご案内|各位"
)
VENUE_MARK_RE = re.compile(
    r"カフェ|cafe|Caffe|STORE|店|TOKYO|cosme|IDEE|スタジオ|studio|ビル|ホール|クラブ|センター",
    re.I,
)
NOTE_NAME_RE = re.compile(r"相談可能|団体名|制作会社名|平日は")
TAG_SOUP_RE = re.compile(r"会社|オフィス|駐車場|屋上|外観")
ARCHIVE_SITES = {"番組別アーカイブ", "制作部ロケ地台帳"}
JUNK_SHORT = {
    "道路", "住宅", "ビル", "路地", "坂道", "階段", "店舗", "公園", "空地", "民家",
    "農道", "洋館", "畑", "団地", "農地", "倉庫", "橋", "農家", "墓地", "埠頭",
    "海岸", "海", "茶畑", "踏切", "教室", "廊下", "医局", "項目", "紹介", "以降",
    "金額", "電車", "バス", "病院", "関文", "機内", "樽", "神社",
}


def compact_name(name: str) -> str:
    return re.sub(r"\s+", "", (name or "").strip())


def _squash(text: str) -> str:
    return re.sub(r"[\s　]+", "", text or "")


def is_office_document(name: str, address: str = "", site: str = "") -> bool:
    """経理・契約・行政手続きの文面。施設名そのものに含まれる殿・No.・@は残す。"""
    raw_name = name or ""
    raw_address = address or ""
    blob = _squash(raw_name) + _squash(raw_address)
    if OFFICE_WORD_RE.search(blob):
        return True
    if VERSION_RE.search(raw_name) or VERSION_RE.search(raw_address):
        return True
    if HONORIFIC_DONO_RE.search(raw_name):
        return True
    if PAGE_SLASH_RE.search(raw_name):
        return True
    compact = _squash(raw_name)
    catalog_name = site == "全国ロケ地ガイド" and len(compact) < 35
    venue_name = (
        site not in ARCHIVE_SITES
        and len(compact) <= 40
        and not re.search(r"撮影場所|週刊|PV|申請|領収|請求|誓約|登記|車両|場面|配車|氏名", raw_name)
        and bool(re.search(r"東京都|北海道|府|県|区|市|丁目|国外", raw_address))
    )
    if not catalog_name and not venue_name:
        if re.search(r"No\.", raw_name, re.I):
            building = re.search(r"No\.\s*\d", raw_name, re.I) and VENUE_MARK_RE.search(raw_name)
            studio = re.search(r"(?:STUDIO|Studio|スタジオ).{0,24}No\.\s*[A-Za-z0-9]", raw_name)
            if not (building or studio):
                return True
        if "@" in raw_name or "＠" in raw_name:
            if re.search(r"撮影場所|PV|LIVE|feat\.|blog", raw_name, re.I) or not VENUE_MARK_RE.search(raw_name):
                return True
    if len(compact) >= 35 and (site in ARCHIVE_SITES or PROSE_RE.search(raw_name)):
        return True
    if NOTE_NAME_RE.search(raw_name) or len(TAG_SOUP_RE.findall(raw_name)) >= 3:
        return True
    return False


def is_bad_facility_name(name: str) -> bool:
    """取り込み時に施設名へしない行。"""
    raw = (name or "").strip()
    compact = compact_name(raw)
    if len(compact) <= 1:
        return True
    if len(compact) <= 2 and (compact in JUNK_SHORT or not re.fullmatch(r"[一-龥々]{2}", compact)):
        return True
    if compact in JUNK_SHORT:
        return True
    if IMPORT_ROLE_RE.search(raw) or ROLE_P_RE.search(raw):
        return True
    if HEADING_RE.search(raw) or DOC_RE.search(raw) or DOC_RE.search(compact) or DATE_RE.search(raw):
        return True
    if STAFF_RE.search(raw) or EMAIL_RE.search(raw):
        return True
    if len(compact) > 30 and (DATE_RE.search(raw) or DOC_RE.search(raw) or "。" in raw or "ください" in raw or "現場" in raw):
        return True
    if is_office_document(raw):
        return True
    if is_resident_ledger(raw):
        return True
    return False


def person_names(text: str) -> list[str]:
    found = []
    for token in re.findall(r"([一-龥々]{1,5})さん", text or ""):
        if token.endswith(tuple("屋店舗堂館院社園場校寺宮通橋")):
            continue
        found.append(token)
    return found


def is_resident_ledger(name: str, memo: str = "") -> bool:
    """挨拶回り台帳と、部屋番号つきの住民名リスト。"""
    raw_name = name or ""
    blob = f"{raw_name}\n{memo or ''}"
    if "挨拶回り" in raw_name or "菓子折りリスト" in blob:
        return True
    if re.search(r"挨拶回り", blob) and len(person_names(blob)) >= 2:
        return True
    if len(re.findall(r"(?<![0-9０-９])(?!19|20)[0-9０-９]{2,3}[\s　]*[一-龥々]{1,4}さん", blob)) >= 2:
        return True
    names = set(person_names(blob))
    if len(names) >= 3 and re.search(r"補償関連|近隣の\s*方|住民の\s*方|挨拶回り|菓子折", blob):
        return True
    if len(names) >= 3 and re.search(r"sche\.xlsx|スケジュール", raw_name, re.I):
        return True
    return False


def is_contaminated_record(name: str, memo: str = "", site: str = "") -> bool:
    """検索と削除で落とす、日程・連絡先・短すぎる汎用ラベル。"""
    raw = (name or "").strip()
    compact = compact_name(raw)
    note = memo or ""
    blob = f"{raw}\n{note}"
    if len(compact) <= 1:
        return True
    if len(compact) <= 2 and (compact in JUNK_SHORT or not re.fullmatch(r"[一-龥々]{2}", compact)):
        return True
    if compact in JUNK_SHORT:
        return True
    if DATE_RE.search(raw) or DOC_RE.search(raw) or DOC_RE.search(compact) or HEADING_RE.search(raw):
        return True
    if STAFF_RE.search(blob) or "弓田" in blob or EMAIL_RE.search(blob):
        return True
    if re.search(r"[@＠]\s*(山田|弓田|佐野|内田)", blob):
        return True
    if site in ARCHIVE_SITES and ("@" in blob or "＠" in blob):
        return True
    if len(compact) > 30 and site in ARCHIVE_SITES and not FACILITY_HINT_RE.search(compact):
        return True
    if len(compact) > 30 and (DATE_RE.search(raw) or DOC_RE.search(raw) or "ください" in raw or "恐れ入り" in raw):
        return True
    if is_office_document(raw, "", site):
        return True
    if is_resident_ledger(raw, note):
        return True
    return False
