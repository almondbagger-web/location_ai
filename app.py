from __future__ import annotations

import base64
import html
import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from google import genai
from google.genai import types

SCENE_QUOTE_RE = re.compile(r"[「『][^」』]{0,40}[」』]")
SCENE_DATE_RE = re.compile(
    r"(?:令和|平成|昭和)\s*\d+\s*年|\d{4}\s*年|"
    r"\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
)
SCENE_CREDIT_RE = re.compile(r"映画|ドラマ|主演|出演|作品|放送|フジテレビ|日テレ|テレビ朝日|TBS|NHK")
SCENE_RULES = (
    ("取調室", r"取調|取調べ|尋問|留置"),
    ("逃走", r"逃走|追跡|チェイス"),
    ("オフィス会議", r"会議|ミーティング|商談|面談"),
    ("オフィス執務", r"オフィス|執務|デスク|事務室"),
    ("屋上対峙", r"屋上"),
    ("病院ロビー", r"ロビー|待合"),
    ("病室", r"病室|病棟|手術室"),
    ("教室", r"教室|校舎|授業"),
    ("事件現場", r"事件|検証|犯行"),
    ("アクション", r"爆破|爆発|アクション|格闘|戦闘"),
    ("階段", r"階段"),
    ("橋", r"橋"),
    ("公園", r"公園"),
    ("外観", r"外観"),
    ("港", r"港|埠頭|水上"),
    ("警察署", r"警察|警視|交番"),
    ("駅", r"駅|ホーム|改札"),
    ("レストラン", r"レストラン|食堂|カフェ|喫茶"),
    ("住宅内", r"居間|リビング|キッチン|寝室"),
    ("対話", r"対話|会話|会見"),
    ("対峙", r"対峙|対決|緊迫"),
    ("通り", r"通り|街並み|商店街"),
    ("広場", r"広場"),
)
SCENE_QUERIES = (
    ("取調べ", ("取調", "取調べ")),
    ("取調", ("取調", "取調べ")),
    ("屋上", ("屋上",)),
    ("爆破", ("爆破", "爆発", "アクション")),
    ("対決", ("対峙", "対決")),
    ("緊迫", ("対峙", "対決", "緊迫")),
    ("逃走", ("逃走", "追跡")),
    ("会議", ("会議", "オフィス")),
    ("病室", ("病室", "病棟")),
    ("ロビー", ("ロビー", "待合")),
    ("事件", ("事件", "検証")),
    ("教室", ("教室", "校舎")),
    ("オフィス", ("オフィス", "執務")),
)


def abstract_scene(text: str) -> str:
    raw = SCENE_QUOTE_RE.sub(" ", str(text or ""))
    raw = SCENE_DATE_RE.sub(" ", raw)
    kept = []
    for part in re.split(r"[。／/\n]", raw):
        piece = part.strip()
        if not piece or SCENE_CREDIT_RE.search(piece):
            continue
        kept.append(piece)
    blob = " ".join(kept)
    labels = []
    for label, pattern in SCENE_RULES:
        if re.search(pattern, blob) and label not in labels:
            labels.append(label)
        if len(labels) >= 3:
            break
    return " / ".join(labels) if labels else "汎用撮影"


def scene_search_terms(query: str) -> list[str]:
    found = []
    raw = query or ""
    for needle, terms in SCENE_QUERIES:
        if needle not in raw:
            continue
        for term in terms:
            if term not in found:
                found.append(term)
    return found[:6]

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "locations_master.db"
LOGO_PATH = BASE / "logo.png"
AVATAR_PATH = BASE / "assets" / "avatar.png"
CM_PATH = BASE / "assets" / "almondbagger_cm.mp4"
CIRCUIT_PATH = BASE / "assets" / "circuit.svg"

load_dotenv(BASE / ".env")

# サイドバーだけ本番スロット。中央のPRは自社バナーのまま。
ADSENSE_CLIENT = "ca-pub-3232944598825832"
ADSENSE_SLOT_MAIN = "0000000000"
ADSENSE_SLOT_SIDE = "3888878698"
YOUTUBE_MV_URL = "https://youtube.com/@akbb_a.i.kuchipakubandbrothers?si=pk7gAKZ_JMLz1q_2"
SHARE_PAGE = "https://yumi-cha.streamlit.app/"
MAP_TOOL_URL = "https://map.almondbagger.com/"
OFFICIAL_SITE_URL = "https://www.almondbagger.com/"
FLAGSHIPS = (
    ("map", "AB式ロケ地図ツクール", "香盤とロケ地図を、無料で。", MAP_TOOL_URL),
    ("house", "ALMONDBAGGER", "公式サイト", OFFICIAL_SITE_URL),
    ("akbb", "いいねの大盤振る舞い", "公式MV", YOUTUBE_MV_URL),
)

SYSTEM_INSTRUCTION = """
あなたは映像制作会社 株式会社ALMONDBAGGERのAI制作アシスタント『YUMI-CHA』です。映画・ドラマ・CM・MVの制作部現場を20年以上支えてきたベテランの知見と、Google Gemini本来の洗練された知性・論理的思考を持っています。

・トーン：知的で親しみやすく、結論ファースト。余計な自分語りや長い前置きはせず、現場の急ぎの状況を察してスパッと要点を伝える。
・相談・文章作成時：道路使用許可のコツ、挨拶文の作成、段取りの悩みに対して、プロの制作部視点から具体的で無駄のないアドバイスや、コピペしやすいフォーマットを即座に提示する。文面は ``` で囲む。
・ロケ地相談時：撮影日、未解禁を含む作品名、俳優名、撮影実績には触れない。個人の自宅は出さない。案内はロケ地（施設・店舗・公園の正式名称）、住所（都道府県と市区町村）、シーン（取調室、屋上対峙、オフィス会議のような状況名）、条件だけ。シーンが特定できなければ「汎用撮影」。特徴の長文は書かない。新宿区・港区のような行政区名だけを施設名にしない。
・「該当する実データはございません」「施設データはございません」「記録がありません」など、候補が無いと終わる返答は禁止。
・ロケ地の一覧はアプリが箇条書きにする。JSON、配列、{"name": ...} は絶対に書かない。
・1施設の条件やシーンを聞かれたら、その施設だけを文章で答える。撮影受入条件（土日祝、夜間、事前申請）と、向いているシーン、搬入の要点を書く。作品名、撮影日、URLは書かない。
・お弁当は1店舗につき1ブロック。店舗名、エリア、価格帯、配達条件を分け、1行に複数店をカンマやスラッシュで繋げない。最低注文数は実データに無い数字を作らない。
・電話番号、メールアドレス、私的な個人名、部屋番号、台本のシーン番号は書かない。連絡先は「連絡先は社内台帳で確認します」。
・URL、出典は書かない。実データにない日数、料金、最低注文数は作らない。
・代表：YUMITA（弓田）。株式会社ALMONDBAGGER 代表取締役。映画・ドラマの制作部として20年以上、ロケ手配、許認可、香盤、予算、現場統括を担う。八王子フィルムコミッションと連携し、AI Previs と AI VFX も手がける。アーティストとしても音楽活動をしている。肩書きの「日本一の制作部（自称）」「天才AIクリエイター（自称）」は本人の自称。
・「YUMITAとは誰か」「弓田とは誰か」と聞かれたら、この代表情報だけを使い、制作部・代表としての経歴と実績を短く具体的に答える。私的な連絡先や電話番号は出さない。
"""

MIC_HTML = """
<style>
  html, body { margin: 0; background: transparent; overflow: hidden; }
  button {
    width: 44px; height: 44px; border-radius: 50%;
    border: 1px solid #e6ebf0; background: #fff; cursor: pointer;
    box-shadow: 0 6px 16px rgba(15, 23, 42, 0.06);
    display: flex; align-items: center; justify-content: center; padding: 0;
  }
  button:hover { border-color: #c4161c; }
  svg { width: 20px; height: 20px; fill: #4b5563; }
</style>
<button id="mic" type="button" title="音声で入力" aria-label="音声で入力">
  <svg viewBox="0 0 24 24"><path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V20H9v2h6v-2h-2v-2.08A7 7 0 0 0 19 11h-2z"/></svg>
</button>
<script>
const btn = document.getElementById("mic");
btn.onclick = () => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { btn.title = "このブラウザは音声入力に未対応です"; return; }
  const rec = new SR();
  rec.lang = "ja-JP";
  rec.interimResults = false;
  rec.onresult = (event) => {
    const text = event.results[0][0].transcript;
    try {
      const url = new URL(window.parent.location.href);
      url.searchParams.set("speech", text);
      window.parent.location.href = url.toString();
    } catch (err) {
      btn.title = text;
    }
  };
  rec.onerror = () => { btn.style.borderColor = "#e6ebf0"; };
  rec.start();
  btn.style.borderColor = "#c4161c";
};
</script>
"""


def file_b64(path: Path) -> str:
    if not path.exists():
        return ""
    return base64.b64encode(path.read_bytes()).decode()


def is_bento_query(text: str) -> bool:
    if re.search(r"弁当|ロケ弁|ケータリング|朝弁|昼弁|夜弁|仕出し|べんとう|ご飯|ごはん|めし", text):
        return True
    return bool(re.search(r"飯(?!田|能|橋)", text))


def is_manual_query(text: str) -> bool:
    return bool(re.search(r"手続き|許可|香盤|マニュアル|申請|届出|仮払|精算|道路使用|挨拶", text))


PHONE_RE = re.compile(r"0\d{1,4}[-(]?\d{1,4}[-)]?\d{3,4}")
EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
MENTION_RE = re.compile(r"[@＠]\s*(?:氏名|弓田|山田|[一-龥]{2,4})")
STAFF_NAME_RE = re.compile(r"弓田\s*悠太")
SCHEDULE_FACILITY_RE = re.compile(r"月日|号館|お願い|注意|階(?!段)")
PERMIT_LEAK_RE = re.compile(r"[^。\n]*(?:道路使用|申請手順|申請マニュアル|許可台帳|中日は|中日\d)[^。\n]*[。]?")
SAFE_LINE = "アーカイブ書庫より安全に候補を抽出中です。時間を置いて再試行してください。"


OFFICIAL_MAIL = "almondbagger@gmail.com"


def redact_private(text: str) -> str:
    raw = str(text or "")
    raw = raw.replace(OFFICIAL_MAIL, "\u0000OFFICIALMAIL\u0000")
    raw = EMAIL_RE.sub("", raw)
    raw = PHONE_RE.sub("[連絡先は社内台帳参照]", raw)
    raw = MENTION_RE.sub("", raw)
    raw = STAFF_NAME_RE.sub("", raw)
    return raw.replace("\u0000OFFICIALMAIL\u0000", OFFICIAL_MAIL)


def is_founder_query(text: str) -> bool:
    raw = text or ""
    if is_bento_query(raw) or re.search(r"撮影できる|ロケ地|撮影場所|ロケハン", raw):
        return False
    about = re.search(r"誰|どんな|について|教え|紹介|プロフィール|何者|って|とは", raw)
    if re.search(r"YUMITA|ユミタ|ゆみた|弓田", raw, re.I) and about:
        return True
    if re.search(r"製作者|創始者|開発者", raw) and about:
        return True
    return bool(re.search(r"誰が作った|作った人", raw))


def founder_card_html() -> str:
    return """
<article class="founder-card">
  <p class="founder-kicker">PRODUCTION BRAIN</p>
  <h3>【PRODUCTION BRAINの創始者：YUMITA】</h3>
  <p class="founder-joke">肩書きの「自称」は、本人が一番大きく書いています。</p>
  <p class="founder-row"><span>役職</span>株式会社ALMONDBAGGER 代表取締役</p>
  <p class="founder-row"><span>肩書き</span>日本一の制作部（自称） / 多彩な才能を持つ天才AIクリエイター（自称）</p>
  <p class="founder-label">経歴と現在</p>
  <ul>
    <li>20年間にわたり映画・ドラマの現場最前線を支えてきた制作のスペシャリスト。</li>
    <li>現在は現場の知見をテクノロジーに昇華させつつ、アーティストとして音楽活動も展開中。</li>
  </ul>
  <div class="founder-links">
    <a href="https://www.almondbagger.com/" target="_blank" rel="noopener noreferrer">🌐 会社公式HP・実績</a>
    <a class="artist" href="https://www.tunecore.co.jp/artists?id=1169004" target="_blank" rel="noopener noreferrer">🎵 TuneCore アーティストページ</a>
    <a href="https://map.almondbagger.com/" target="_blank" rel="noopener noreferrer">🗺️ ロケ地図ツクール</a>
  </div>
</article>
"""


CONSULT_RE = re.compile(
    r"道路使用|占用|申請|手続き|書き方|どう書け|消防|自治体|"
    r"挨拶状|挨拶文|挨拶|文面|メール|文章|企画書|書いて|作って|雛形|テンプレ|下書き|文案|"
    r"トラブル|スケジュール|香盤|悩み|愚痴|雑談|どうすれば|相談|考えて"
)
LOCATION_ASK_RE = re.compile(
    r"ロケ地|撮影できる|撮影場所|ロケハン|"
    r"(?:居酒屋|学校|小学校|中学校|高校|大学|病院|神社|寺|公園|カフェ).{0,8}(?:教えて|探して|どこ|候補|場所)"
)
PROCEDURE_RE = re.compile(
    r"申請|手続き|書き方|どう書け|挨拶|文面|メール|企画書|トラブル|悩み|愚痴|道路使用|香盤|占用"
)


PLACE_HEADS = (
    "小学校", "中学校", "高等学校", "専門学校", "高校", "大学", "学校",
    "会議室", "オフィス", "スタジオ", "居酒屋", "カフェ", "喫茶店", "喫茶",
    "レストラン", "食堂", "ホテル", "旅館", "病院", "神社", "寺",
    "公園", "ビル", "ホール", "マンション", "アパート",
    "バー", "クラブ", "工場", "倉庫", "商店街",
)
STYLE_HINTS = ("ガラス", "おしゃれ", "お洒落", "モダン", "デザイン", "夜景", "自然光", "高層", "吹き抜け")
GREETING_RE = re.compile(
    r"(?:おはよう(?:ございます)?|お疲れ(?:様|さま)?(?:です)?|こんにちは|こんばんは|よろしく(?:お願いします|お願いいたします)?|ありがとう(?:ございます)?|おやすみ(?:なさい)?)"
    r"[！!。〜ー]*"
)


def place_heads(query: str) -> list[str]:
    raw = query or ""
    found = [head for head in PLACE_HEADS if head in raw]
    found.sort(key=len, reverse=True)
    kept = []
    for head in found:
        if any(head != other and head in other for other in found):
            continue
        kept.append(head)
    return kept[:2]


DESK_RE = re.compile(
    r"爆破|火薬|"
    r"道路封鎖|公道ストップ|公道.{0,8}封鎖|封鎖できる|"
    r"制作部|業務相談|応援|ヘルプ|制作統括|"
    r"AI\s*クリエイター|AI\s*コンテ|AI\s*映像|AI\s*制作|Previs|VFX"
)
DESK_TASK = (
    "門前払いしない。現実的な一次候補か要点を、2文か3文だけ書く。"
    "長い手順、講釈、個人名、電話番号、メールアドレスは書かない。"
    "爆破や火薬なら、栃木・岩舟山、採石場の跡地、専用スタジオの範囲で言い、住宅地と公道では扱わないと添える。"
    "道路封鎖なら、公道の完全封鎖は極めてハードルが高いことと、地方FCの協力道路、敷地内の私道を挙げる。"
    "応援や制作統括なら、日程、人数、役割を先に揃える要点だけ。"
    "AI映像なら、用途と尺を確認する一文で終える。"
    "相談窓口の案内は別枠で出すので、ここでは書かない。"
)


def is_desk_query(text: str) -> bool:
    return bool(DESK_RE.search(text or ""))


def is_greeting(text: str) -> bool:
    raw = re.sub(r"\s+", "", text or "")
    return bool(raw and GREETING_RE.fullmatch(raw))


def is_scene_search(text: str) -> bool:
    raw = text or ""
    if not scene_search_terms(raw):
        return False
    if re.search(r"作り方|配合|合成|原料|手順", raw):
        return False
    return bool(re.search(r"シーン|場所|どこ|撮影|できる|ロケ|探", raw))


def is_location_query(text: str) -> bool:
    raw = text or ""
    if is_scene_search(raw):
        return True
    if is_bento_query(raw) or is_founder_query(raw) or is_greeting(raw) or is_desk_query(raw):
        return False
    if PROCEDURE_RE.search(raw):
        return False
    if is_more_request(raw):
        return more_has_target(raw)
    if LOCATION_ASK_RE.search(raw):
        return True
    if not place_heads(raw):
        return False
    if len(raw) <= 48:
        return True
    return bool(re.search(r"ない|ある|探し|教えて|候補|どこ|撮影|ロケ|ほしい|欲しい", raw))


CRAFT_FOLLOW_RE = re.compile(
    r"教えて|ください|下さい|出して|見せて|フォーマット|テンプレ|雛形|文面|全文|書いて|お願い"
)
GREETING_LETTER = """【ドラマ・映像撮影ロケーションのお願い（近隣挨拶文テンプレ）】

拝啓
時下ますますご清栄のこととお慶び申し上げます。
この度、下記の通り弊社製作の映像作品の撮影をさせて頂く事になりましたので、ご挨拶申し上げます。
近隣の皆様にご迷惑をおかけしないよう細心の注意を払い、安全に配慮して進行いたしますが、何卒ご理解とご協力の程お願い申し上げます。
何かご不明な点やお気づきの点がございましたら、現場スタッフまたは下記担当者までご一報いただけますようお願い申し上げます。
お騒がせ致しますが、重ねてご協力の程よろしくお願い申し上げます。
敬具

記
■ 日時：
  ・準備日：令和〇年〇月〇日（〇） 〇〇:〇〇 〜 〇〇:〇〇（予定）
  ・撮影日：令和〇年〇月〇日（〇） 〇〇:〇〇 〜 〇〇:〇〇（予定）
  ・予備日：令和〇年〇月〇日（〇） 〇〇:〇〇 〜 〇〇:〇〇（予定）

■ 場所：
  [撮影現場名・建物名]（住所：[現場住所]）

■ 撮影内容：
  ドラマ（映画/CM/MV）のシーン撮影（※車両停車、照明、小道具搬入等）

■ 制作プロダクション：
  株式会社[制作会社名]
  住所：〒XXX-XXXX [会社住所]
  電話番号：[会社電話番号]

■ 現場責任者（制作担当）：
  [担当者氏名]
  携帯番号：[携帯電話番号]
  メールアドレス：[連絡先メールアドレス]
"""
CALLSHEET_NOTE = """香盤は、撮影日の進行表です。次の順で1枚にまとめます。

・見出し：作品名、撮影日、集合時間、集合場所、天候予備
・時間の列：入り、食事、仕込み、リハーサル、本番、移動、終了
・各行：時刻、シーン、内容、出演、場所、衣装・美術・車両、備考
・食事と移動と車両を、シーンより先に固定する
・変わった箇所は版を上げ、古い版と混ざらないようにする

角括弧の固有名詞は、その日の香盤に書き写してください。"""
ROAD_NOTE = """道路使用許可は、撮影場所を管轄する警察署の窓口です。公園や河川は、道路とは別の管理者になります。

・全面通行止めに近い規制は、極めてハードルが高いです。短時間の駐停車、誘導、片側交互を先に相談します。
・許可が下りる前に、近隣へ確定日程として知らせない。
・近隣挨拶、許可証の携帯、誘導員をセットで考えます。
・住宅地の深夜帯は避ける。緊急車両の通り道は塞がない。

申請書の空欄は、現場名・日時・車両台数・駐停車位置が分かってから埋めます。"""
MINDSET_NOTE = """制作部の心得は、現場を止めないことです。

・朝の確認は、危険、近隣、許可、食事、車両の順。
・不明なことは隠さず、その場で担当へ返す。
・未公開の作品名、出演者、個人の連絡先は、必要な相手以外に出さない。
・挨拶と香盤と許可が揃ってから、美術と車両を動かす。
・困ったら一人で抱えず、株式会社ALMONDBAGGERの制作へ相談する。"""


def classify_craft(text: str) -> str:
    raw = text or ""
    if re.search(r"香盤", raw):
        return "callsheet"
    if re.search(r"道路使用|占用", raw):
        return "road"
    if re.search(r"心得", raw):
        return "mindset"
    if re.search(r"近隣.{0,8}挨拶|挨拶文|挨拶状|お願い状|ご挨拶", raw):
        return "greeting"
    if re.search(r"テンプレート|テンプレ|フォーマット|雛形|文面|文案", raw):
        return "greeting"
    return ""


def is_craft_followup(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact or len(compact) > 28:
        return False
    if is_bento_query(text) or is_founder_query(text) or is_location_query(text):
        return False
    return bool(CRAFT_FOLLOW_RE.search(compact))


def inherited_craft(messages: list[dict] | None) -> str:
    prior = list(messages or [])
    if prior and prior[-1].get("role") == "user":
        prior = prior[:-1]
    for message in reversed(prior):
        if message.get("role") != "user":
            continue
        kind = classify_craft(str(message.get("content") or ""))
        if kind:
            return kind
        break
    for message in reversed(prior):
        if message.get("role") != "assistant":
            continue
        lead = re.sub(r"<[^>]+>", " ", str(message.get("content") or ""))[:80]
        return classify_craft(lead)
    return ""


def craft_kind(user_text: str, messages: list[dict] | None = None) -> str:
    direct = classify_craft(user_text)
    if direct:
        return direct
    if not is_craft_followup(user_text):
        return ""
    return inherited_craft(messages)


def craft_reply(kind: str, messages: list[dict] | None = None) -> tuple[str, bool]:
    try:
        st.session_state.last_craft = kind
        st.session_state.last_match_count = None
    except Exception:
        pass
    if kind == "callsheet":
        lead, body = "香盤の作り方です。", CALLSHEET_NOTE
    elif kind == "road":
        lead, body = "道路使用許可で先に見る注意点です。", ROAD_NOTE
    elif kind == "mindset":
        lead, body = "制作部の心得です。", MINDSET_NOTE
    else:
        lead, body = "近隣挨拶文の実務フォーマットです。〇と角括弧は、その現場の予定に置き換えて使ってください。", GREETING_LETTER
    return (
        f"<div class='bubble'><p>{html.escape(lead)}</p><pre class='craft-doc'>{html.escape(body)}</pre></div>",
        True,
    )


def is_conversation_request(text: str) -> bool:
    raw = text or ""
    if is_bento_query(raw) or is_founder_query(raw) or is_location_query(raw):
        return False
    return True


def schedule_facility(name: str) -> bool:
    return bool(SCHEDULE_FACILITY_RE.search(str(name or "")))


def blocked_place(name: str, address: str = "", site: str = "", detail: str = "") -> bool:
    from location_hygiene import is_office_document, is_resident_ledger

    raw_name = str(name or "").strip()
    if not raw_name or schedule_facility(raw_name):
        return True
    if is_resident_ledger(raw_name, f"{address or ''}\n{detail or ''}"):
        return True
    return is_office_document(raw_name, str(address or ""), str(site or ""))


DATE_LEAK_RE = re.compile(
    r"(?:令和|平成|昭和)\s*\d+\s*年|"
    r"\d{4}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?|"
    r"\d{1,2}\s*月\s*\d{1,2}\s*日|"
    r"[〇○]\s*月\s*[〇○0-9０-９]{0,2}\s*日|"
    r"\d{4}-\d{1,2}-\d{1,2}|"
    r"撮影日|ロケ日|使用日|収録日"
)
TITLE_MARK_RE = re.compile(r"[『「][^』」]{1,40}[』」]")
CREDIT_RE = re.compile(r"撮影実績|過去番組|で使われた|の撮影で|ロケ実績|作品名|主演|出演作品|で撮影")
CONDITION_RE = re.compile(r"土日|祝|平日|夜間|早朝|深夜|撮影可|要相談|要申請|申請|期限|事前|予約|休業|貸切|時間帯|利用条件|受入|受け入れ|曜日")
SPEC_RE = re.compile(r"控室|支度|楽屋|駐車場|大型車|搬入|養生|電源|トイレ|待機|車両|車幅|ゲート|エレベーター|階段|荷下ろし|駐車")


def strip_confidential(text: str) -> str:
    raw = TITLE_MARK_RE.sub("", str(text or ""))
    kept = []
    for part in re.split(r"[。\n]", raw):
        piece = part.strip(" 　")
        if not piece or DATE_LEAK_RE.search(piece) or CREDIT_RE.search(piece):
            continue
        kept.append(piece)
    return re.sub(r"\s+", " ", "。".join(kept)).strip("。 ")


def split_facility_notes(text: str) -> tuple[str, str]:
    cleaned = strip_confidential(text)
    conditions: list[str] = []
    specs: list[str] = []
    for piece in re.split(r"[。／/、]", cleaned):
        bit = piece.strip(" ・")
        if not bit:
            continue
        if CONDITION_RE.search(bit):
            conditions.append(bit)
        elif SPEC_RE.search(bit):
            specs.append(bit)
    unique_cond = list(dict.fromkeys(conditions))
    unique_spec = list(dict.fromkeys(specs))
    return "、".join(unique_cond)[:140], "、".join(unique_spec)[:180]


def public_facility(row: dict) -> tuple[str, str, str, str]:
    name = redact_private(str(row.get("location_name") or "").strip())
    address = redact_private(str(row.get("address") or "").strip())
    blob = "。".join(
        str(row.get(key) or "")
        for key in ("accept_terms", "detail_text", "facility_spec")
        if str(row.get(key) or "").strip()
    )
    conditions, specs = split_facility_notes(blob)
    conditions = re.sub(r"^(?:【利用条件】|利用条件[:：])", "", conditions).strip(" 、")
    specs = re.sub(r"^(?:特徴[:：]|設備[:：])", "", specs).strip(" 、")
    return name, address, conditions, specs


def overlay_facility(into: dict, extra: dict) -> None:
    into["works_title"] = ""
    left_name, left_address, left_cond, left_spec = public_facility(into)
    right_name, right_address, right_cond, right_spec = public_facility(extra)
    conditions = "、".join(dict.fromkeys(part for part in (left_cond, right_cond) if part))
    specs = "、".join(dict.fromkeys(part for part in (left_spec, right_spec) if part))
    into["accept_terms"] = conditions
    into["facility_spec"] = specs
    if right_name and not left_name:
        into["location_name"] = right_name
    if right_address and not left_address:
        into["address"] = right_address
    note = " / ".join(
        part for part in (
            f"利用条件: {conditions}" if conditions else "",
            f"設備: {specs}" if specs else "",
        ) if part
    )
    if note:
        into["detail_text"] = note


def clean_visible_text(text: str) -> str:
    text = str(text or "")
    held: list[str] = []

    def hold(match: re.Match) -> str:
        held.append(match.group(0))
        return f"\u0000F{len(held) - 1}\u0000"

    text = re.sub(r"```.*?```", hold, text, flags=re.S)
    text = re.sub(r"\[\[.*?\]\]", "", text, flags=re.S)
    text = re.sub(r"\[[^\]]+\]\((https?://[^\s)]+)\)", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"(?im)^(?!.*\[連絡先は社内台帳参照\]).*?(?:出典|参照|参考リンク|参考文献|ソース|Sources|Grounding).*$", "", text)
    text = re.sub(r"\[\d+\]", "", text)
    text = redact_private(text)
    text = strip_confidential(text)
    text = re.sub(r"\u0000F(\d+)\u0000", lambda match: held[int(match.group(1))], text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


BENTO_EMPTY_RE = re.compile(r"リストに記載なし|記載なし|記載はない|^なし$|^none$|^null$|^空欄?$|^-$", re.I)
BENTO_STAFF_RE = re.compile(r"村国|大川|ヒガシ|タカスギ|池本|発注表|の名前|発注メモ|担当者?[:：]|担当メモ")
BENTO_PERSON_RE = re.compile(r"[一-龥ァ-ヶA-Za-z]{1,8}さん")


def clean_bento_clause(text: str) -> str:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw or raw.lower() in {"none", "null"}:
        return ""
    kept = []
    for part in re.split(r"[。／/]", raw):
        piece = part.strip(" ・,、")
        if not piece or BENTO_EMPTY_RE.search(piece):
            continue
        if BENTO_STAFF_RE.search(piece) or BENTO_PERSON_RE.search(piece):
            continue
        if re.search(r"電話|連絡|携帯|社内台帳", piece) or PHONE_RE.search(piece):
            continue
        kept.append(piece)
    return "。".join(kept)


def isolate_bento(text: str) -> str:
    kept = []
    for line in clean_visible_text(text).splitlines():
        if re.search(r"ロケ地|施設|撮影場所|現場メモ|シーン|住所", line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def loosen_candidates(text: str) -> str:
    raw = str(text or "")
    if re.search(r"名前\s*[:：]", raw):
        raw = re.sub(r"[ \t]+(?=(?:\*{1,2}|[-・])\s*名前\s*[:：])", "\n", raw)
        raw = re.sub(r"(?m)^[ \t]*\*{1,2}[ \t]+", "- ", raw)
        raw = re.sub(r"(?m)^[ \t]*名前\s*[:：]", "- ", raw)
        return raw
    if raw.count(" / ") >= 2:
        raw = re.sub(r"\s+-\s+", "\n- ", raw)
    return raw


def candidate_item(line: str) -> str:
    body = re.sub(r"^[-・]\s*", "", line).strip()
    body = re.sub(r"^名前\s*[:：]\s*", "", body)
    parts = [part.strip() for part in re.split(r"\s+[-・／/]\s+", body) if part.strip()]
    if not parts:
        return ""
    name = re.sub(r"^(?:施設名|店名)\s*[:：]\s*", "", parts[0]).strip()
    subs = []
    for part in parts[1:]:
        if ":" in part or "：" in part:
            label, value = re.split(r"\s*[:：]\s*", part, maxsplit=1)
            label, value = label.strip(), value.strip()
        else:
            label, value = "", part
        if not value or re.search(r"記載なし|データなし|^なし$", value):
            continue
        subs.append(f"{label} {value}".strip() if label else value)
    html_name = html.escape(name)
    address = ""
    badges = []
    for bit in subs:
        label = ""
        value = bit
        if re.match(r"^(所在地|住所|実績|価格|発注条件|おすすめ)\s+", bit):
            label, value = bit.split(" ", 1)
        if label in {"所在地", "住所"} or (not label and re.search(r"〒|都|道|府|県|区|市|町|村", value)):
            if not address:
                address = value.strip()
            continue
        if label == "実績" or CREDIT_RE.search(value) or DATE_LEAK_RE.search(value):
            continue
        if label in {"利用条件", "発注条件"} or CONDITION_RE.search(value):
            shown = strip_confidential(value.strip())
            if shown:
                badges.append(f"<span class='spot-badge'>【利用条件】{html.escape(shown)}</span>")
            continue
        if SPEC_RE.search(value):
            badges.append(f"<span class='spot-badge'>{html.escape(value.strip())}</span>")
            continue
        if label == "価格":
            badges.append(f"<span class='spot-badge'>🍱 {html.escape(value.strip())}</span>")
    addr_html = f"<p class='spot-addr'>エリア: {html.escape(address)}</p>" if address else ""
    badge_html = f"<p class='spot-badges'>{''.join(badges)}</p>" if badges else ""
    return f"<article class='spot'><h3>{html_name}</h3>{addr_html}{badge_html}</article>"


def render_prose(chunk: str) -> str:
    chunk = loosen_candidates(chunk)
    escaped_lines = []
    items = []
    blocks = []

    def flush_items():
        if items:
            blocks.append("<div class='spot-list'>" + "".join(items) + "</div>")
            items.clear()

    for line in chunk.split("\n"):
        stripped = line.strip()
        if re.match(r"^[-・]\s+\S", stripped):
            item = candidate_item(stripped)
            if item:
                items.append(item)
            continue
        flush_items()
        if stripped:
            escaped_lines.append(stripped)
    flush_items()
    if escaped_lines:
        prose = []
        for line in escaped_lines:
            safe = html.escape(line)
            safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
            safe = re.sub(r"^#{1,6}\s*", "", safe)
            prose.append(safe)
        blocks.insert(0, "<br>".join(prose))
    return "".join(blocks)


def rich_text(text: str) -> str:
    raw = clean_visible_text(text)
    pieces = []
    cursor = 0
    for match in re.finditer(r"```(?:[a-zA-Z0-9_-]+)?\s*\n?(.*?)```", raw, re.S):
        pieces.append(("text", raw[cursor:match.start()]))
        pieces.append(("code", match.group(1).strip("\n")))
        cursor = match.end()
    pieces.append(("text", raw[cursor:]))
    rendered = []
    for kind, chunk in pieces:
        if not chunk:
            continue
        if kind == "code":
            body = html.escape(chunk).replace("\n", "<br>")
            rendered.append(f"<div class='copy-block'>{body}</div>")
            continue
        rendered.append(render_prose(chunk))
    return "".join(rendered)


def keywords(query: str) -> list[str]:
    parts = [part for part in re.split(r"\s+", query.strip()) if part]
    if len(parts) >= 2:
        return parts[:4]
    stop = {"ロケ", "ロケ地", "場所", "教えて", "ください", "お願い", "ドラマ", "今期", "候補", "撮影", "シーン", "附近", "あたり"}
    try:
        from janome.tokenizer import Tokenizer

        if not hasattr(keywords, "_tokenizer"):
            keywords._tokenizer = Tokenizer()
        words = []
        for token in keywords._tokenizer.tokenize(query):
            part = token.part_of_speech
            if not part.startswith("名詞"):
                continue
            if any(flag in part for flag in ("非自立", "代名詞", "数")):
                continue
            word = token.surface.strip()
            if len(word) < 2 or word in stop:
                continue
            words.append(word)
        if words:
            return words[:4]
    except Exception:
        pass
    return [query.strip()[:40]] if query.strip() else []


def init_system_tables():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS search_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_query TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_learned_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                work_name TEXT,
                address TEXT,
                description TEXT,
                source_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        columns = {row[1] for row in cur.execute("PRAGMA table_info(search_logs)")}
        if "reply" not in columns:
            cur.execute("ALTER TABLE search_logs ADD COLUMN reply TEXT")
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_table_columns(table_name="locations") -> list[str]:
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        conn.close()
        return [row[1] for row in rows]
    except Exception:
        return []


SCHOOL_QUERY_RE = re.compile(r"小学校|中学校|高等学校|高校|大学|学校|学園|校舎|キャンパス|教室")
SCHOOL_TERMS = ("学校", "校舎", "学園", "キャンパス", "教室", "小学校", "中学校", "高校", "高等学校", "大学")
NOISE_TERMS = ("公園", "道路", "通り", "交差点", "橋", "駅")


def like_param(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def is_school_query(query: str) -> bool:
    return bool(SCHOOL_QUERY_RE.search(query))


def like_sql(column: str) -> str:
    return f'"{column}" LIKE ? ESCAPE \'\\\''


def region_rank_sql() -> str:
    address = 'IFNULL("address", \'\')'
    return (
        "CASE "
        f"WHEN {address} LIKE '%東京%' THEN 1 "
        f"WHEN {address} LIKE '%神奈川%' OR {address} LIKE '%千葉%' OR {address} LIKE '%埼玉%' THEN 2 "
        "ELSE 3 END"
    )


def school_filter_sql(columns: set[str]) -> tuple[str, list[str]]:
    positive_cols = [name for name in ("location_name", "scenes_category") if name in columns]
    if not positive_cols or "location_name" not in columns:
        return "", []
    positive = []
    params: list[str] = []
    for term in SCHOOL_TERMS:
        for column in positive_cols:
            positive.append(like_sql(column))
            params.append(like_param(term))
    noise = [like_sql("location_name") for _ in NOISE_TERMS]
    params.extend(like_param(term) for term in NOISE_TERMS)
    keep = [f'"location_name" NOT LIKE ? ESCAPE \'\\\'' for _ in SCHOOL_TERMS]
    params.extend(like_param(term) for term in SCHOOL_TERMS)
    clause = (
        " AND (" + " OR ".join(positive) + ")"
        + " AND NOT ((" + " OR ".join(noise) + ") AND " + " AND ".join(keep) + ")"
    )
    return clause, params


def acceptable_school_row(row: dict) -> bool:
    name = str(row.get("location_name") or "")
    compact = re.sub(r"\s+", "", name)
    if compact in {"学校", "小学校", "中学校", "高校", "大学", "高等学校"}:
        return False
    if not any(term in name for term in SCHOOL_TERMS):
        return False
    if any(term in name for term in ("公園", "道路", "通り", "交差点", "歩道橋")):
        return False
    return True


PLACE_FILLER = {
    "設定", "施設", "できる", "場所", "撮影", "シーン", "候補", "教えて", "ください", "お願い",
    "他", "ほか", "もっと", "ない", "無い", "見せて", "別", "追加", "もう",
}
MORE_RE = re.compile(
    r"他(?:居酒屋|に|の|は|も)*ない|ほか(?:に|の|は|も)*ない|もっと(?:教えて|見せて|見たい|出して|ください|下さい)|別の.{0,12}(?:ない|見せて|教えて)"
)
FORM_NAME_RE = re.compile(r"許可申請|始末書|申請者|別記様式|香盤|連絡網|クレジット|名簿|警察署長|止め撮り|走行撮影|代表取締役|御中")
PRIVATE_NAME_RE = re.compile(r"自宅|個人宅|実家|表札|号室")
CATEGORY_SYNONYMS = (
    ("学校", ("小学校", "中学校", "高校", "高等学校", "大学", "廃校", "校舎", "学園")),
    ("オフィス", ("ビル", "会議室", "事務所", "執務室")),
    ("会社", ("ビル", "会議室", "事務所", "執務室", "オフィス")),
    ("病院", ("医院", "クリニック", "病棟", "病室")),
    ("家", ("民家", "一軒家", "ハウススタジオ", "古民家", "アパート")),
)
EMPTY_HIT_RE = re.compile(
    r"該当する.{0,24}(?:ございません|ありません)|実データはございません|施設データはございません|"
    r"記録がまだありません|一致する記録がまだ|データはございません|見つかりません"
)
KNOWHOW_RE = re.compile(r"駐車|養生|消えモノ|消モノ|時間制限|撮影時間|台数|搬入")


def mentions_category(query: str, key: str) -> bool:
    raw = query or ""
    if key == "家":
        return bool(re.search(r"(?<![一-龥])家(?![族電具主政])", raw))
    return key in raw


def category_terms(query: str) -> list[str]:
    found: list[str] = []
    for key, words in CATEGORY_SYNONYMS:
        if not mentions_category(query, key):
            continue
        seeds = words if key == "家" else (key, *words)
        for word in seeds:
            if word not in found:
                found.append(word)
    return found


def search_terms(query: str) -> list[str]:
    heads = place_heads(query)
    if heads:
        return heads
    matched = re.search(r"([一-龥ぁ-んァ-ヶA-Za-z0-9]{2,12})設定", query or "")
    terms = [term for term in keywords(query) if term not in PLACE_FILLER and len(term) <= 16]
    if matched:
        head = matched.group(1)
        if head not in PLACE_FILLER:
            terms = [head] + [term for term in terms if term != head]
    kept = []
    for term in terms:
        if any(term != other and term in other for other in terms):
            continue
        if MORE_RE.search(term):
            continue
        term = re.sub(r"^(?:他|ほか|別)", "", term)
        if len(term) < 2:
            continue
        kept.append(term)
    if is_more_request(query):
        for place in PLACE_TAIL_RE.findall(query or ""):
            place = re.sub(r"^(?:他|ほか|別)", "", place)
            if place and place not in kept:
                kept.insert(0, place)
    folded = []
    for term in kept:
        if any(term != other and term in other for other in kept):
            continue
        if term not in folded:
            folded.append(term)
    return folded[:3]


def acceptable_public_row(row: dict) -> bool:
    from location_hygiene import is_contaminated_record

    name = str(row.get("location_name") or "").strip()
    compact = re.sub(r"\s+", "", name)
    if len(name) < 2 or len(compact) > 80:
        return False
    if FORM_NAME_RE.search(compact) or PRIVATE_NAME_RE.search(compact):
        return False
    address = str(row.get("address") or "")
    if PRIVATE_NAME_RE.search(address):
        return False
    site = str(row.get("site_name") or "")
    if is_contaminated_record(name, str(row.get("detail_text") or ""), site):
        return False
    if blocked_place(name, address, site, str(row.get("detail_text") or "")):
        return False
    return True


def location_key(row: dict) -> str:
    site = str(row.get("site_name") or "")
    rid = row.get("id")
    if rid not in (None, ""):
        return f"{site}:{rid}"
    name = str(row.get("location_name") or "").strip()
    address = str(row.get("address") or "").strip()
    return f"name:{name}|{address}"


def headed_place(row: dict, heads: list[str]) -> bool:
    name = str(row.get("location_name") or "")
    compact = re.sub(r"\s+", "", name)
    if not any(head in name for head in heads):
        return False
    if re.fullmatch(r"[大小中]?会議室|円卓会議室|リモート会議室|オフィスビル|オフィス|スタジオ", compact):
        return False
    if not str(row.get("address") or "").strip() and len(compact) < 10:
        return False
    if re.search(r"検索|ネット|について|する方法|一覧", name):
        return False
    labels = set(re.findall(r"駐車場|屋上|外観|警察署|会議室|オフィス|会社|ホール", name))
    if len(labels) >= 3:
        return False
    return True


def search_local_locations(query: str, limit: int = 6, exclude_keys: set[str] | None = None) -> list[dict]:
    exclude_keys = {str(key) for key in (exclude_keys or set()) if key}
    columns = set(get_table_columns("locations"))
    use = (["id"] if "id" in columns else []) + [
        name for name in ("location_name", "address", "works_title", "scenes_category", "detail_text", "site_name") if name in columns
    ]
    scene_terms = scene_search_terms(query)
    terms = scene_terms or search_terms(query)
    school = is_school_query(query)
    if not use or (not terms and not school):
        return []
    search_cols = [name for name in use if name not in ("site_name", "works_title")]
    quoted = ", ".join(f'"{name}"' for name in use)
    school_sql, school_params = school_filter_sql(columns) if school else ("", [])
    order_sql = f" ORDER BY {region_rank_sql()}, id" if "address" in columns else " ORDER BY id"
    unlimited = int(limit or 0) <= 0
    fetch_limit = 200000 if unlimited else min(8000, max(800, int(limit)))
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    def run(active_terms: list[str], joiner: str):
        clauses = []
        params: list[str] = []
        for term in active_terms:
            clauses.append("(" + " OR ".join(like_sql(name) for name in search_cols) + ")")
            params.extend([like_param(term)] * len(search_cols))
        where = " AND ".join(clauses) if joiner == "AND" else " OR ".join(clauses)
        if not where:
            where = "1=1"
        primary = active_terms[0] if active_terms else ""
        rank_sql = order_sql
        rank_params: list[str] = []
        if primary and "location_name" in columns:
            rank_sql = " ORDER BY CASE WHEN " + like_sql("location_name")
            rank_params.append(like_param(primary))
            if "scenes_category" in columns:
                rank_sql += " OR " + like_sql("scenes_category")
                rank_params.append(like_param(primary))
            rank_sql += " THEN 0 ELSE 1 END, "
            if "site_name" in columns:
                rank_sql += "CASE WHEN site_name = '番組別アーカイブ' THEN 0 ELSE 1 END, "
            rank_sql += f"{region_rank_sql()}, id" if "address" in columns else "id"
        sql = f"SELECT {quoted} FROM locations WHERE ({where}){school_sql}{rank_sql} LIMIT {fetch_limit}"
        return conn.execute(sql, params + school_params + rank_params).fetchall()

    def learned_rows(active_terms: list[str]) -> list[dict]:
        try:
            learned_cols = set(get_table_columns("auto_learned_locations"))
            needed = {"title", "address", "description"}
            if not needed <= learned_cols or not active_terms:
                return []
            fields = ["title", "address", "description"]
            id_sql = "id, " if "id" in learned_cols else ""
            clauses = []
            params: list[str] = []
            for term in active_terms:
                clauses.append("(" + " OR ".join(like_sql(name) for name in fields) + ")")
                params.extend([like_param(term)] * len(fields))
            where = " OR ".join(clauses)
            source = ", source_url" if "source_url" in learned_cols else ""
            learned_cap = 2000 if unlimited else min(400, int(limit) * 8 + len(exclude_keys))
            sql = (
                f"SELECT {id_sql}title, address, description{source} "
                f"FROM auto_learned_locations WHERE ({where}) LIMIT {learned_cap}"
            )
            mapped = []
            for row in conn.execute(sql, params).fetchall():
                source_url = str(row["source_url"] or "") if "source_url" in row.keys() else ""
                mapped.append(
                    {
                        "id": row["id"] if "id" in row.keys() else "",
                        "location_name": row["title"] or "",
                        "address": row["address"] or "",
                        "works_title": "",
                        "accept_terms": "",
                        "facility_spec": "",
                        "scenes_category": "",
                        "detail_text": row["description"] or "",
                        "site_name": (
                    "番組別アーカイブ" if source_url.startswith("local-archive")
                    else "map-tool" if source_url.startswith("map-tool")
                    else "auto_learned"
                ),
                    }
                )
            return mapped
        except Exception:
            return []

    try:
        rows = run(terms, "OR" if scene_terms else "AND") if terms else []
        if not rows and school:
            rows = run([], "AND")
        elif not rows and len(terms) > 1:
            rows = run([max(terms, key=len)], "OR")

        def materialize(raw_rows):
            picked = []
            for row in raw_rows:
                item = {name: row[name] for name in use}
                if "site_name" not in item:
                    item["site_name"] = ""
                item["works_title"] = ""
                item["scene"] = abstract_scene(str(item.get("scenes_category") or ""))
                picked.append(item)
            picked.extend(learned_rows(terms))
            picked = [row for row in picked if acceptable_public_row(row)]
            if school:
                picked = [row for row in picked if acceptable_school_row(row)]
            heads = [] if scene_terms else place_heads(query)
            if heads:
                picked = [row for row in picked if headed_place(row, heads)]
            return picked

        found = materialize(rows)
        if not found and terms:
            found = materialize(run(terms, "OR"))
        if not found and terms:
            found = materialize(run([max(terms, key=len)], "OR"))
        conn.close()
        pool_cap = 200000 if unlimited else max(int(limit), 40)
        ranked = mix_location_hits(found, terms, pool_cap, query)
        if exclude_keys:
            ranked = [row for row in ranked if location_key(row) not in exclude_keys]
        return ranked if unlimited else ranked[: int(limit)]
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return []


def mix_location_hits(rows: list[dict], terms: list[str], limit: int, query: str = "") -> list[dict]:
    primary = terms[0] if terms else ""
    hints = [hint for hint in STYLE_HINTS if hint in (query or "")]
    seen: dict[tuple[str, str], dict] = {}
    unique = []
    for row in rows:
        key = (str(row.get("location_name") or "").strip(), str(row.get("address") or "").strip())
        if key in seen:
            overlay_facility(seen[key], row)
            continue
        overlay_facility(row, row)
        seen[key] = row
        unique.append(row)

    def region(row: dict) -> int:
        address = str(row.get("address") or "")
        if "東京" in address:
            return 0
        if any(name in address for name in ("神奈川", "千葉", "埼玉")):
            return 1
        return 2

    def know(row: dict) -> int:
        return 0 if KNOWHOW_RE.search(str(row.get("detail_text") or "")) else 1

    def strong(row: dict) -> bool:
        if not primary:
            return True
        blob = str(row.get("location_name") or "") + str(row.get("scenes_category") or "")
        return primary in blob

    def styled(row: dict) -> int:
        if not hints:
            return 0
        blob = "".join(str(row.get(key) or "") for key in ("location_name", "scenes_category", "detail_text"))
        return 0 if any(hint in blob for hint in hints) else 1

    def plain(row: dict) -> int:
        name = re.sub(r"\s+", "", str(row.get("location_name") or ""))
        if name in {"オフィス", "会議室", "オフィスビル", "スタジオ", "学校", "小学校", "ビル"}:
            return 1
        return 0 if str(row.get("address") or "").strip() else 1

    unique.sort(key=lambda row: (styled(row), plain(row), 0 if strong(row) else 1, know(row), region(row)))
    archive = [row for row in unique if strong(row) and row.get("site_name") == "番組別アーカイブ"]
    catalog = [row for row in unique if strong(row) and row.get("site_name") != "番組別アーカイブ"]
    weak = [row for row in unique if not strong(row)]
    picked = []
    if archive and catalog:
        ai = ci = 0
        while len(picked) < limit and (ai < len(archive) or ci < len(catalog)):
            if ai < len(archive):
                picked.append(archive[ai])
                ai += 1
            if len(picked) >= limit:
                break
            if ci < len(catalog):
                picked.append(catalog[ci])
                ci += 1
    else:
        picked = (archive or catalog)[:limit]
    if len(picked) < limit:
        picked.extend(weak[: limit - len(picked)])
    return picked[:limit]


COUNT_OR_ADDRESS_RE = re.compile(
    r"\d+\s*丁目|\d+\s*番地|\d+\s*号(?!館)|\d+\s*[台円人名個本階分秒%％]"
    r"|\d{4}\s*年|\d{1,2}\s*月|\d{1,2}\s*日|\d{1,2}\s*:\s*\d{2}"
    r"|\d+\s*[-－]\s*\d+\s*[-－]\s*\d+"
)
PAREN_DIGIT_RE = re.compile(r"[（(\[［][^（）()\[\]［］]*\d[^（）()\[\]［］]*[）)\]］]")
COMMA_NUM_RE = re.compile(r"(?<![\d.])\d{1,4}(?:\s*[,、，]\s*\d{1,4})+(?![\d.])")
RANGE_NUM_RE = re.compile(r"(?<![\d.])\d{1,3}\s*[-–—~～〜－]\s*\d{1,3}(?![\d月年日時分丁目番地号])")
SCENE_LABEL_RE = re.compile(r"(?:シーン番号|シーン|Scene|SCENE|＃|#|No\.?|NO\.?)\s*[:：.]?\s*\d{1,4}")
ISOLATED_NUM_RE = re.compile(r"(?:(?<=[\s　/／|｜、,・])|^)\d{1,3}(?=[\s　/／|｜、,・]|$)")
TRAIL_NUM_RE = re.compile(r"(?<=[ぁ-んァ-ヶ一-龥])\d{1,3}(?=\s*(?:/|／|$|、|。))")
PLACE_TAIL_RE = re.compile(
    r"(?:[一-龥ァ-ヶー]{1,8}の)?"
    r"[一-龥ァ-ヶーA-Za-z0-9]{0,10}"
    r"(?:店内|店外|店頭|個室|室内|外観|屋外|付近|周辺|飲み屋街|飲み屋|"
    r"居酒屋|食堂|レストラン|喫茶店|カフェ|スナック|料亭|ホテル|旅館|民宿|"
    r"駅前|駅|橋の下|橋|公園|校舎|教室|中学校|小学校|高等学校|高校|大学|学校|"
    r"病院|医院|クリニック|アパート|マンション|坂道|川原|通り|路地|オフィス|"
    r"会議室|ビル|神社|寺|牧場|畑|階段|空港|港|滝|墓地|公民館|ロビー|廊下|"
    r"屋上|駐車場|海岸|ビーチ|交番|警察署|警察|市役所|役所|ホール|スタジオ|"
    r"商店街|玄関|倉庫|工場|市場|温泉|プール|体育館|待合|車内|バー|クラブ)"
)
NARRATIVE_RE = re.compile(r"した|していた|行った|訪れた|降り|話をし|見て|待って|歩いて|乗った|会った")


def strip_scene_numbers(text: str) -> str:
    raw = str(text or "")
    held: list[str] = []

    def hold(match: re.Match) -> str:
        held.append(match.group(0))
        return f"\u0000{len(held) - 1}\u0000"

    raw = COUNT_OR_ADDRESS_RE.sub(hold, raw)
    raw = PAREN_DIGIT_RE.sub("", raw)
    raw = COMMA_NUM_RE.sub("", raw)
    raw = RANGE_NUM_RE.sub("", raw)
    raw = SCENE_LABEL_RE.sub("", raw)
    raw = ISOLATED_NUM_RE.sub(" ", raw)
    raw = TRAIL_NUM_RE.sub("", raw)
    raw = re.sub(r"\u0000(\d+)\u0000", lambda match: held[int(match.group(1))], raw)
    raw = re.sub(r"[（(]\s*[）)]", "", raw)
    raw = re.sub(r"[\[［]\s*[\]］]", "", raw)
    raw = re.sub(r"\s*/\s*", " / ", raw)
    raw = re.sub(r"(?:\s*/\s*)+$", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" /・、,")
    return raw.strip()


def public_scene(text: str) -> str:
    cleaned = strip_scene_numbers(text)
    if not cleaned:
        return ""
    parts = [part.strip(" 　、,") for part in re.split(r"\s*/\s*", cleaned) if part.strip(" 　、,")]
    primary = parts[0] if parts else ""
    tags = "、".join(parts[1:]) if len(parts) > 1 else ""
    primary = re.sub(r"(?:の)?シーン$", "", primary).strip(" 　、の")
    found = list(PLACE_TAIL_RE.finditer(primary))
    if found:
        return found[-1].group(0).strip(" 　の")
    if tags:
        tag_hits = list(PLACE_TAIL_RE.finditer(tags))
        if tag_hits:
            return tag_hits[-1].group(0).strip(" 　の")
        if len(tags) <= 24:
            return tags
    if primary and len(primary) <= 22 and not NARRATIVE_RE.search(primary):
        return primary
    return ""


def field_memo(detail: str) -> str:
    from location_hygiene import is_resident_ledger

    if is_resident_ledger("", str(detail or "")):
        return ""
    text = strip_scene_numbers(redact_private(str(detail or "")))
    text = PERMIT_LEAK_RE.sub("", text)
    text = re.sub(r"写真:\s*.*", "", text)
    text = re.sub(r"(制作部|担当者?|連絡先|氏名)\s*[:：]\s*\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" /・")
    if len(text) > 180:
        text = text[:180] + "…"
    return text


def format_archive(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        name = str(row.get("location_name") or "").strip()
        address = str(row.get("address") or "").strip()
        if blocked_place(name, address, str(row.get("site_name") or ""), str(row.get("detail_text") or "")):
            continue
        _, _, conditions, specs = public_facility(row)
        if not any((name, address, conditions, specs)):
            continue
        lines.append(
            f"- 施設: {name} / 住所: {address}"
            + (f" / 利用条件: {conditions}" if conditions else "")
            + (f" / 設備: {specs}" if specs else "")
        )
    return "\n".join(lines)


def location_cards_html(rows: list[dict], with_lead: bool = True) -> str:
    cards = []
    for row in rows:
        raw_name = redact_private(str(row.get("location_name") or "").strip())
        raw_address = redact_private(str(row.get("address") or "").strip())
        raw_detail = str(row.get("detail_text") or "")
        if blocked_place(raw_name, raw_address, str(row.get("site_name") or ""), raw_detail):
            continue
        safe_name, safe_address, conditions, specs = public_facility(row)
        name = html.escape(safe_name or raw_name)
        address = html.escape(safe_address or raw_address)
        address_html = f"<p class='loc-address'>{address}</p>" if address else ""
        cond_html = f"<p class='loc-meta'><span>利用条件</span>{html.escape(conditions)}</p>" if conditions else ""
        spec_html = f"<p class='loc-meta'><span>設備</span>{html.escape(specs)}</p>" if specs else ""
        cards.append(
            "<article class='loc-card'>"
            f"<h3 class='loc-name'>{name}</h3>"
            f"{address_html}"
            f"{cond_html}"
            f"{spec_html}"
            "</article>"
        )
    if not cards:
        return ""
    body = "<div class='loc-cards'>" + "".join(cards) + "</div>"
    if not with_lead:
        return body
    return "<p class='loc-lead'>近い候補です。</p>" + body


def location_list_html(rows: list[dict], more: bool = False, with_lead: bool = True) -> str:
    items = []
    for row in rows:
        raw_name = redact_private(str(row.get("location_name") or "").strip())
        address = redact_private(str(row.get("address") or "").strip())
        if blocked_place(raw_name, address, str(row.get("site_name") or ""), str(row.get("detail_text") or "")):
            continue
        _, _, conditions, specs = public_facility(row)
        line = f"<strong>{html.escape(raw_name)}</strong>"
        if address:
            line += f"（{html.escape(address)}）"
        if conditions:
            line += f" <span class='loc-work'>| 【利用条件】{html.escape(conditions)}</span>"
        if specs:
            line += f" <span class='loc-work'>| {html.escape(specs)}</span>"
        items.append(f"<li>{line}</li>")
    if not items:
        return ""
    body = "<ul class='loc-list'>" + "".join(items) + "</ul>"
    if not with_lead:
        return body
    lead = "ほかの候補です。" if more else "近い候補です。"
    return f"<p class='loc-lead'>{lead}</p>" + body


def is_more_request(text: str) -> bool:
    return bool(MORE_RE.search(text or ""))


def remember_locations(rows: list[dict], query: str, more: bool):
    try:
        prev = list(st.session_state.get("shown_locations") or []) if more else []
        keys = [location_key(row) for row in rows]
        st.session_state.shown_locations = prev + [key for key in keys if key not in prev]
        st.session_state.last_location_query = query
        listed = []
        for row in rows[:120]:
            listed.append(
                {
                    "location_name": str(row.get("location_name") or ""),
                    "address": str(row.get("address") or ""),
                    "scene": str(row.get("scene") or row.get("scenes_category") or ""),
                    "condition": str(row.get("condition") or row.get("accept_terms") or ""),
                    "facility_spec": str(row.get("facility_spec") or ""),
                }
            )
        st.session_state.last_listed_places = listed
    except Exception:
        pass


def shown_location_keys(more: bool) -> set[str]:
    if not more:
        return set()
    try:
        return {str(key) for key in (st.session_state.get("shown_locations") or [])}
    except Exception:
        return set()


def followup_query(user_text: str) -> str:
    extra = search_terms(user_text)
    if extra:
        return " ".join(extra)
    try:
        return str(st.session_state.get("last_location_query") or "")
    except Exception:
        return ""


def more_has_target(user_text: str) -> bool:
    if search_terms(user_text):
        return True
    try:
        return bool(st.session_state.get("last_location_query"))
    except Exception:
        return False


def log_search_query(query: str) -> int | None:
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT INTO search_logs (user_query) VALUES (?)", (query,))
        row_id = cur.lastrowid
        conn.commit()
        conn.close()
        return row_id
    except Exception:
        return None


def save_log_reply(row_id: int | None, reply: str):
    if not row_id:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE search_logs SET reply = ? WHERE id = ?", (reply[:8000], row_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


def save_new_location(title, work_name, address, description):
    conditions, specs = split_facility_notes(str(description or ""))
    safe_desc = " / ".join(
        part for part in (
            f"利用条件: {conditions}" if conditions else "",
            f"設備: {specs}" if specs else "",
        ) if part
    )
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """
            INSERT INTO auto_learned_locations (title, work_name, address, description, source_url)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, "", address, safe_desc, "Google Search Auto-Crawl"),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def absorb_new_location(reply: str, allow_save: bool) -> str:
    match = re.search(r"\[\[NEW_LOCATION:\s*(\{.*?\})\s*\]\]", reply, flags=re.S)
    if not match:
        return reply
    if allow_save:
        try:
            data = json.loads(match.group(1))
            save_new_location(
                data.get("title", ""),
                "",
                data.get("address", ""),
                data.get("description", ""),
            )
        except Exception:
            pass
    return reply.replace(match.group(0), "")


def knowledge_bundle(query: str, bento: bool, manual: bool):
    try:
        from guild_knowledge import prompt_context, shop_order_fields
    except Exception:
        return "", [], None
    text, shops, _chunks = prompt_context(query, include_shops=bento, include_manuals=manual)
    return text, shops, shop_order_fields


def shops_to_text(shops, shop_order_fields, query: str = "") -> str:
    if not shops or shop_order_fields is None:
        return "エリアと予算が分かると、近い店を並べます。"
    blocks = []
    for shop in shops:
        try:
            fields = shop_order_fields(shop)
        except Exception:
            continue
        lines = [f"**{shop.get('name') or '店名未記載'}**"]
        price = clean_bento_clause(fields.get("価格帯"))
        if price:
            lines.append(f"- 価格帯: {price}")
        order_bits = []
        for chunk in re.split(r"\s*/\s*", str(fields.get("配達エリア・最低注文個数") or "")):
            cleaned = clean_bento_clause(chunk)
            if cleaned:
                order_bits.append(cleaned)
        deadline = clean_bento_clause(fields.get("注文締切"))
        if deadline:
            order_bits.append(deadline)
        if order_bits:
            lines.append("- 発注条件: " + " / ".join(order_bits))
        menu = clean_bento_clause(fields.get("おすすめメニュー"))
        if menu:
            lines.append(f"- おすすめ: {menu}")
        appeal = clean_bento_clause(fields.get("現場ウケのポイント"))
        if appeal:
            lines.append(f"- 現場ウケ: {appeal}")
        if len(lines) > 1:
            blocks.append("\n".join(lines))
    if not blocks:
        return "エリアと予算が分かると、近い店を並べます。"
    blocks.append("予算とエリアが近い店から、個数と締切を確認して当たってください。")
    blocks = [isolate_bento(block) for block in blocks if isolate_bento(block)]
    try:
        from guild_knowledge import fc_bento_lines
        support = fc_bento_lines(query)
    except Exception:
        support = ""
    if support:
        cleaned = clean_bento_clause(support)
        if cleaned and "リストに記載なし" not in cleaned:
            blocks.append(cleaned)
    return "\n\n".join(blocks)


def gemini_api_key() -> str:
    secret = ""
    try:
        secret = str(st.secrets.get("GEMINI_API_KEY", "") or "")
    except Exception:
        secret = ""
    return (secret or os.getenv("GEMINI_API_KEY") or "").strip()


def gemini_model_name() -> str:
    secret = ""
    try:
        secret = str(st.secrets.get("GEMINI_MODEL", "") or "")
    except Exception:
        secret = ""
    return (secret or os.getenv("GEMINI_MODEL") or "").strip()


def model_chain() -> list[str]:
    chain = ["gemini-flash-lite-latest"]
    configured = gemini_model_name()
    if configured and configured not in chain:
        chain.append(configured)
    return chain


def api_is_quota(message: str) -> bool:
    return "429" in message or "RESOURCE_EXHAUSTED" in message


def api_is_missing(message: str) -> bool:
    return "404" in message or "not found" in message.lower()


def looks_like_failure(text: str) -> bool:
    return bool(re.search(r"応答を作れ|通信枠|GEMINI|APIキー|\.env|traceback|Exception|NOT_FOUND|RESOURCE_EXHAUSTED|エラー|429|404|st\.error", text or "", re.I))


RAMBLE_RE = re.compile(r"お疲れ様|胃がキリキリ|（笑）|\(笑\)|了解です|大変ですよね|本当にお疲れ")


def guard_speech(text: str) -> str:
    from location_hygiene import person_names

    cleaned = re.sub(r"```(?:json)?\s*.*?```", "", str(text or ""), flags=re.S | re.I)
    cleaned = re.sub(r"\[\s*\{[\s\S]*?\}\s*\]", "", cleaned)
    cleaned = re.sub(r"\{[^{}]{0,500}\"(?:name|address|condition)\"[^{}]{0,500}\}", "", cleaned)
    cleaned = strip_scene_numbers(clean_visible_text(cleaned))
    if len(set(person_names(cleaned))) >= 2:
        cleaned = re.sub(r"[一-龥々]{1,5}さん", "", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def history_block(messages: list[dict]) -> str:
    lines = []
    for message in messages[:-1][-8:]:
        content = str(message.get("content") or "")
        if content.startswith("こんにちは! AIアシスタント"):
            continue
        who = "ユーザー" if message.get("role") == "user" else "YUMI-CHA"
        lines.append(f"{who}: {guard_speech(content)[:500]}")
    return "\n".join(lines)


def bento_facts(user_text: str) -> str:
    try:
        _knowledge, shops, shop_fields = knowledge_bundle(user_text, True, False)
        text = isolate_bento(shops_to_text(shops, shop_fields, user_text))
    except Exception:
        return "（該当する店の記録なし）"
    for canned in (
        "エリアと予算が分かると、近い店を並べます。",
        "予算とエリアが近い店から、個数と締切を確認して当たってください。",
    ):
        text = text.replace(canned, "")
    return text.strip() or "（該当する店の記録なし）"


def bento_cards_html(user_text: str) -> str:
    try:
        from guild_knowledge import bento_for_query, shop_order_fields
    except Exception:
        return ""
    try:
        shops = bento_for_query(user_text, limit=40)
    except Exception:
        shops = []
    cards = []
    for shop in shops or []:
        try:
            fields = shop_order_fields(shop)
        except Exception:
            continue
        name = str(shop.get("name") or "").strip()
        if not name:
            continue
        area = clean_bento_clause(str(shop.get("area") or ""))
        if re.search(r"番号|電話", area):
            area = ""
        title = html.escape(name)
        if area:
            title += f"（{html.escape(area)}）"
        lines = [f"<h3>🍱 {title}</h3>"]
        price = clean_bento_clause(str(fields.get("価格帯") or ""))
        if price and "記載なし" not in price:
            lines.append(f"<p class='spot-feature'>価格帯: {html.escape(price)}</p>")
        delivery = []
        for key in ("配達エリア・最低注文個数", "注文締切"):
            bit = clean_bento_clause(str(fields.get(key) or ""))
            if bit and "記載なし" not in bit and not re.search(r"電話|連絡", bit):
                delivery.append(bit)
        if delivery:
            lines.append(f"<p class='spot-addr'>配達条件: {html.escape('、'.join(delivery))}</p>")
        menu = clean_bento_clause(str(fields.get("おすすめメニュー") or ""))
        if menu and "記載なし" not in menu:
            lines.append(f"<p class='spot-addr'>メニュー: {html.escape(menu)}</p>")
        if len(lines) > 1:
            cards.append("<article class='spot bento-card'>" + "".join(lines) + "</article>")
    if not cards:
        return ""
    return "<div class='spot-list'>" + "".join(cards) + "</div>"


def bento_reply(user_text: str, messages: list[dict] | None = None) -> str:
    cards = bento_cards_html(user_text)
    if cards:
        return f"<div class='bubble'>店ごとに整理したロケ弁です。</div>{cards}"
    spoken = ask_gemini(
        user_text,
        messages or [],
        None,
        None,
        bento_facts(user_text),
        "お弁当は1店舗につき1ブロック。店名、エリア、価格帯、配達条件を改行し、複数店を1行に繋げない。電話番号は書かない。",
    )
    return spoken


def place_brief(rows: list[dict]) -> str:
    lines = []
    for row in rows[:8]:
        name, address, conditions, specs = public_facility(row)
        if not name:
            continue
        bits = [name]
        if address:
            bits.append(address)
        if conditions:
            bits.append(f"利用条件: {conditions}")
        if specs:
            bits.append(f"設備: {specs}")
        lines.append("- " + " / ".join(bits))
    return "\n".join(lines)


def ask_gemini(
    user_text: str,
    messages: list[dict],
    image_bytes: bytes | None,
    mime: str | None,
    facts: str = "",
    task: str = "",
) -> str:
    guided = bool(task)
    prior = history_block(messages)
    has_records = bool(facts) and "該当する" not in facts and facts.strip() != "（なし）"
    if not task and has_records:
        task = (
            "実データが渡されている。このデータに無い施設名、店名、数字は作らない。"
            "紹介は2文まで。そのあと候補を箇条書きにする。"
            "1件につき必ず1行。行頭は「- 」。"
            "JSONや {\"name\": ...} は書かない。ロケ地の列挙はしない。お弁当なら1店舗につき1ブロックで、店名、エリア、価格帯、配達条件を改行する。"
            "撮影日と作品名は書かない。1行の中に次の候補を続けない。"
        )
    elif not task:
        task = "実データの一覧は渡していない。施設名や店名を作って候補として出さない。相談、挨拶、文章作成には直接答える。"
    prompt = f"""
{task}

【これまでの会話】
{prior or "（なし）"}

【質問】
{user_text}

【実データ】
{facts or "（なし）"}
"""
    client = None
    try:
        client = st.session_state.get("gemini_client")
    except Exception:
        client = None
    if client is None:
        return ""
    contents = [prompt]
    if image_bytes:
        contents = [types.Part.from_bytes(data=image_bytes, mime_type=mime or "image/png"), prompt]
    for model in model_chain():
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.4 if facts or guided else 0.6,
                    max_output_tokens=1600 if "10件" in (task or "") else (900 if guided else (1600 if facts else 800)),
                ),
            )
            reply_text = guard_speech(response.text or "")
            if reply_text and not looks_like_failure(reply_text):
                return reply_text
        except Exception:
            continue
    return ""


def desk_seed(user_text: str) -> str:
    raw = user_text or ""
    if re.search(r"爆破|火薬", raw):
        return "爆破の実写は、栃木・岩舟山、採石場の跡地、専用スタジオが現実的です。住宅地と公道では扱いません。"
    if re.search(r"封鎖|公道ストップ", raw):
        return "公道の完全封鎖は極めてハードルが高いです。代替は、地方FCの協力道路と、敷地内の私道です。"
    if re.search(r"AI|Previs|VFX", raw):
        return "AI PrevisとAI VFXは、画の用途と尺が分かると進め方が決まります。"
    return "応援は、日程、人数、役割の3点が先です。そこが揃うと、現場に入れる人の当たりがつきます。"


def brief_desk(text: str) -> str:
    cleaned = guard_speech(text)
    if not cleaned:
        return ""
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) == 1:
        sentences = re.findall(r"[^。]+。", lines[0])
        if len(sentences) > 3:
            return "".join(sentences[:3])
    return "\n".join(lines[:4])


def desk_card_html() -> str:
    from urllib.parse import quote

    mail = (
        "mailto:almondbagger@gmail.com?subject="
        + quote("【撮影・制作相談】")
    )
    return (
        "<aside class='desk-card'>"
        "<p class='desk-note'>※公道の完全封鎖や爆破等の特殊撮影、また現場の緊急応援や制作統括、"
        "AI映像制作など、どうしても実現したい案件は、現場のプロが何でも直接ご相談を受け付けています。</p>"
        "<h3>株式会社ALMONDBAGGER 現場・制作相談窓口</h3>"
        "<ul>"
        "<li>特殊撮影（爆破・道路封鎖・大規模ロケ）の交渉・許認可</li>"
        "<li>制作部の現場統括 / 急な人数応援・ヘルプ要員</li>"
        "<li>次世代AIクリエイティブ（AI Previs / AI VFX）制作</li>"
        "</ul>"
        "<div class='desk-actions'>"
        f"<a class='desk-mail' href='{mail}'>📩 メールで相談する: almondbagger@gmail.com</a>"
        "<a class='desk-web' href='https://www.almondbagger.com/' target='_blank' rel='noopener noreferrer'>🌐 公式HP: https://www.almondbagger.com/</a>"
        "</div>"
        "</aside>"
    )


def desk_reply(user_text: str, messages: list[dict] | None = None) -> tuple[str, bool]:
    spoken = brief_desk(ask_gemini(user_text, messages or [], None, None, task=DESK_TASK))
    if not spoken:
        spoken = desk_seed(user_text)
    return f"<div class='bubble'>{rich_text(spoken)}</div>{desk_card_html()}", True


def conversation_reply(
    user_text: str,
    messages: list[dict],
    image_bytes: bytes | None,
    mime: str | None,
    mode: str = "talk",
    extra: str = "",
) -> str:
    return ask_gemini(user_text, messages, image_bytes, mime, extra)


PLACE_TAIL_RE = re.compile(
    r"ヒルズ|モール|ビル|タワー|ホール|センター|スタジオ|公園|病院|大学|高校|学校|会館|スタジアム|UDX|プラザ|スクエア|ハウス|旅館|ホテル|神社|寺院|ドーム|体育館"
)
PLACE_CHUNK_RE = re.compile(
    r"[一-龥A-Za-z0-9・]{1,12}(?:" + PLACE_TAIL_RE.pattern + r")"
)
PREFECTURES = (
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
)
PREFECTURE_RE = re.compile("|".join(PREFECTURES))
ADMIN_NAME_RE = re.compile(
    rf"^(?:{'|'.join(PREFECTURES)})?(?:[一-龥]{{1,8}}(?:郡|市|区|町|村)){{1,3}}$"
)
GENERIC_PLACE_NAMES = {"学校", "ビル", "病院", "家", "オフィス", "会社", "民家"}


def split_glued_places(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    found = []
    cursor = 0
    while cursor < len(raw):
        match = PLACE_TAIL_RE.search(raw, cursor)
        if not match:
            break
        chunk = raw[cursor:match.end()].strip(" 　、/・")
        if chunk:
            found.append(chunk)
        cursor = match.end()
    if len(found) >= 2:
        return found
    return [raw]


def is_admin_name(text: str) -> bool:
    compact = re.sub(r"[\s　]", "", text or "")
    if not compact:
        return False
    if compact in PREFECTURES:
        return True
    return bool(ADMIN_NAME_RE.fullmatch(compact))


def is_address_text(text: str) -> bool:
    compact = re.sub(r"[\s　\u200e]", "", text or "")
    if not compact or is_admin_name(compact):
        return bool(compact)
    if PLACE_TAIL_RE.search(compact) or re.search(r"店|食堂|カフェ|区立|市立", compact):
        return False
    if re.search(r"〒|\d{3}-\d{4}|丁目|番地", compact):
        return True
    if PREFECTURE_RE.search(compact) and re.search(r"(?:市|区|町|村)", compact):
        return True
    return False


def is_note_token(text: str) -> bool:
    compact = str(text or "")
    if PLACE_CHUNK_RE.search(compact) and not CONDITION_RE.search(compact):
        return False
    return bool(CONDITION_RE.search(compact) or re.search(r"受入|撮影可|相談", compact))


def format_address(text: str) -> str:
    raw = re.sub(r"[\s　\u200e]", "", str(text or ""))
    raw = re.sub(r"^〒?\d{3}-?\d{4}", "", raw)
    pref = ""
    rest = raw
    matched = PREFECTURE_RE.search(raw)
    if matched:
        pref = matched.group(0)
        rest = raw[matched.end():]
        while rest.startswith(pref):
            rest = rest[len(pref):]
    locals_ = re.findall(r"[一-龥]{1,6}(?:市|区|町|村)", rest)
    local = locals_[0] if locals_ else ""
    if len(locals_) >= 2 and local.endswith(("市", "郡")) and locals_[1].endswith(("区", "町", "村")):
        local = local + locals_[1]
    shown = f"{pref}{local}"
    while pref and shown.startswith(pref + pref):
        shown = shown[len(pref):]
    return shown


def short_condition(text: str, own_name: str = "") -> str:
    bits = []
    for piece in re.split(r"[。／/、\n]", str(text or "")):
        bit = piece.strip(" 　・")
        bit = re.sub(r"^(?:【利用条件】|利用条件[:：]|撮影受入条件[:：]|条件[:：])", "", bit).strip()
        if not bit or DATE_LEAK_RE.search(bit) or CREDIT_RE.search(bit):
            continue
        foreign = [item for item in PLACE_CHUNK_RE.findall(bit) if own_name not in item]
        if foreign:
            continue
        if not CONDITION_RE.search(bit) and "相談" not in bit:
            continue
        if len(bit) > 24:
            bit = bit[:24]
        if bit not in bits:
            bits.append(bit)
        if len("、".join(bits)) >= 36:
            break
    return "、".join(bits)[:40]


def clean_facility_name(text: str) -> str:
    name = DATE_LEAK_RE.sub("", str(text or ""))
    name = re.sub(r"^(?:ロケ地|施設名|店名|名称|名前)\s*[:：]\s*", "", name).strip(" 　・")
    extra = ""

    def grab(match: re.Match) -> str:
        nonlocal extra
        inner = match.group(1).strip()
        if is_admin_name(inner) or is_address_text(inner):
            extra = extra or inner
            return ""
        return match.group(0)

    name = re.sub(r"[（(]([^）)]+)[）)]", grab, name).strip(" 　")
    matched = re.match(
        rf"^((?:{'|'.join(PREFECTURES)})?(?:[一-龥]{{1,6}}(?:市|区|町|村)){{1,2}})[の\s]+(.+)$",
        name,
    )
    if matched and not is_admin_name(matched.group(2)):
        name = matched.group(2).strip()
    return re.sub(r"\s+", "", name)


def valid_facility_name(name: str) -> bool:
    compact = re.sub(r"\s+", "", name or "")
    if len(compact) < 2 or len(compact) > 80:
        return False
    if compact in GENERIC_PLACE_NAMES or is_admin_name(compact) or is_address_text(compact):
        return False
    if re.search(r"〒|丁目|番地|にある|の中央|および|など", compact):
        return False
    if EMPTY_HIT_RE.search(compact):
        return False
    return True


def expand_place_rows(row: dict) -> list[dict]:
    peeled = clean_facility_name(str(row.get("location_name") or ""))
    names = split_glued_places(peeled)
    if len(names) < 2:
        names = [peeled] if peeled else []
    address = format_address(str(row.get("address") or ""))
    condition = short_condition(
        str(row.get("accept_terms") or row.get("condition") or ""),
        peeled,
    )
    scene = abstract_scene(str(row.get("scene") or row.get("scenes_category") or ""))
    many = len([name for name in names if valid_facility_name(clean_facility_name(name))]) >= 2
    found = []
    for raw in names:
        name = clean_facility_name(raw)
        if not valid_facility_name(name):
            continue
        item = {
            "location_name": name,
            "address": "" if many else address,
            "accept_terms": "" if many else condition,
            "condition": "要相談" if many or not condition else condition,
            "scene": "汎用撮影" if many else scene,
            "facility_spec": "",
            "feature": "",
            "detail_text": "",
            "works_title": "",
            "site_name": row.get("site_name") or "",
        }
        if row.get("id") not in (None, "") and not many:
            item["id"] = row.get("id")
        if acceptable_public_row(item):
            found.append(item)
    return found


def facility_triples(parts: list[str]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    name = address = note = ""
    held_address = ""

    def flush() -> None:
        nonlocal name, address, note, held_address
        if valid_facility_name(clean_facility_name(name)):
            rows.append((clean_facility_name(name), address or held_address, note))
        name = address = note = ""
        held_address = ""

    for part in parts:
        part = part.strip()
        if not part:
            continue
        glued = split_glued_places(part)
        if len(glued) >= 2 and not is_note_token(part) and not is_admin_name(part):
            if name:
                flush()
            for chunk in glued:
                if name:
                    flush()
                name = chunk
            continue
        if is_admin_name(part) or is_address_text(part):
            shown = format_address(part) or part
            if not name:
                held_address = shown
            elif not address:
                address = shown
            continue
        if is_note_token(part):
            if name:
                note = f"{note}、{part}".strip("、") if note else part
            continue
        if name:
            flush()
        name = part
    flush()
    return rows


def mask_public(text: str) -> str:
    raw = TITLE_MARK_RE.sub("", str(text or ""))
    raw = DATE_LEAK_RE.sub("", raw)
    raw = CREDIT_RE.sub("", raw)
    raw = PHONE_RE.sub("", raw)
    raw = EMAIL_RE.sub("", raw)
    raw = PRIVATE_NAME_RE.sub("", raw)
    raw = re.sub(r"[一-龥々]{1,5}さん", "", raw)
    return re.sub(r"\s+", "", raw).strip("・、/／")


def showable_name(name: str) -> bool:
    compact = re.sub(r"\s+", "", name or "")
    if len(compact) < 2 or len(compact) > 80:
        return False
    if compact in GENERIC_PLACE_NAMES or is_admin_name(compact):
        return False
    if PRIVATE_NAME_RE.search(compact):
        return False
    if re.fullmatch(r"[0-9０-９〒\-ー]+", compact):
        return False
    if DATE_LEAK_RE.search(compact) or TITLE_MARK_RE.search(name or ""):
        return False
    return True


def bullet_lines(row: dict) -> list[str]:
    places = expand_place_rows(row)
    if not places:
        name = mask_public(clean_facility_name(str(row.get("location_name") or "")))
        if not showable_name(name):
            return []
        places = [{
            "location_name": name,
            "address": row.get("address") or "",
            "scene": row.get("scene") or row.get("scenes_category") or "",
        }]
    lines = []
    for place in places:
        name = mask_public(str(place.get("location_name") or ""))
        if not showable_name(name):
            continue
        address = format_address(mask_public(str(place.get("address") or ""))) or "要確認"
        scene = abstract_scene(mask_public(str(place.get("scene") or place.get("scenes_category") or "")))
        scene = (scene or "汎用撮影").replace(" / ", "・")
        lines.append(f"・{html.escape(name)}：{html.escape(address)}【{html.escape(scene)}】")
    return lines


def spot_cards_html(rows: list[dict]) -> str:
    lines = []
    seen = set()
    for row in rows:
        for line in bullet_lines(row):
            key = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", line))
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"<li>{line}</li>")
    if not lines:
        return ""
    return "<ul class='line-list'>" + "".join(lines) + "</ul>"


def drop_empty_claims(text: str) -> str:
    kept = []
    for piece in re.split(r"(?<=。)", str(text or "")):
        if EMPTY_HIT_RE.search(piece):
            continue
        kept.append(piece)
    return "".join(kept).strip()


def unique_places(rows: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for row in rows:
        name = re.sub(r"\s+", "", str(row.get("location_name") or ""))
        if not name or name in seen:
            continue
        seen.add(name)
        unique.append(row)
    return unique


def parse_location_lines(text: str) -> list[dict]:
    raw = loosen_candidates(EMPTY_HIT_RE.sub("", text or ""))
    raw = re.sub(r"[ \t]+(?=[-・]\s+)", "\n", raw)
    rows = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        body = re.sub(r"^[-・]\s*", "", stripped)
        body = re.sub(r"^\d{1,2}[\.．、]\s*", "", body)
        parts = [part.strip() for part in re.split(r"\s*[/／]\s*", body) if part.strip()]
        if len(parts) == 1:
            matched = re.match(r"(.+?)[（(]([^）)]+)[）)]\s*(.*)", parts[0])
            if matched:
                parts = [matched.group(1).strip(), matched.group(2).strip(), matched.group(3).strip()]
        for name, area, rest in facility_triples(parts):
            name = DATE_LEAK_RE.sub("", re.sub(r"^(?:施設名|店名)\s*[:：]\s*", "", name)).strip(" 　")
            if len(name) < 2 or EMPTY_HIT_RE.search(name) or name in {"学校", "ビル", "病院", "家", "オフィス", "会社", "民家"}:
                continue
            area = DATE_LEAK_RE.sub("", strip_confidential(area)).strip()
            rest = strip_confidential(rest)
            conditions, specs = split_facility_notes(rest)
            feature = rest.split("【利用条件】", 1)[0].strip(" 。、/") if rest else ""
            row = {
                "location_name": name,
                "address": area,
                "works_title": "",
                "accept_terms": conditions,
                "facility_spec": specs,
                "feature": feature,
                "detail_text": rest,
                "site_name": "kanto-public",
            }
            if re.search(r"自宅|個人宅|実家", name + area):
                continue
            if not acceptable_public_row(row):
                continue
            rows.append(row)
    return unique_places(rows)


def prefer_tokyo(rows: list[dict]) -> list[dict]:
    def rank(row: dict) -> int:
        address = str(row.get("address") or "")
        if "東京" in address:
            return 0
        if any(name in address for name in ("神奈川", "千葉", "埼玉")):
            return 1
        if any(name in address for name in ("茨城", "栃木", "群馬")):
            return 2
        return 3

    return sorted(rows, key=rank)


MASTER_CACHE: dict[str, object] = {"at": 0.0, "rows": []}


def csv_cell(cells: list[str], index: int) -> str:
    if index < 0 or index >= len(cells):
        return ""
    return str(cells[index] or "").strip()


def read_csv_table(url: str) -> list[list[str]]:
    import csv
    import io
    import urllib.request

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "YUMI-CHA/1.0"})
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read(12_000_000)
            kind = str(response.headers.get("Content-Type") or "")
        if payload.lstrip()[:1] == b"<" or ("html" in kind.lower() and "csv" not in kind.lower()):
            return []
        text = payload.decode("utf-8-sig", errors="replace")
        if text.lstrip()[:1] == "<":
            return []
        return list(csv.reader(io.StringIO(text)))
    except Exception:
        return []


def refresh_csv_bundle() -> None:
    import time
    from concurrent.futures import ThreadPoolExecutor

    now = time.monotonic()
    if (SHEET_CACHE.get("rows") or MASTER_CACHE.get("rows")) and now - float(SHEET_CACHE.get("at") or 0) < 1800:
        return
    sheet_table: list[list[str]] = []
    master_table: list[list[str]] = []
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            sheet_job = pool.submit(read_csv_table, SHEET1_CSV)
            master_job = pool.submit(read_csv_table, MASTER_CSV)
            sheet_table = sheet_job.result(timeout=16) or []
            master_table = master_job.result(timeout=16) or []
    except Exception:
        sheet_table, master_table = [], []
    sheet_rows_parsed: list[dict] = []
    for cells in sheet_table[1:]:
        try:
            name = csv_cell(cells, 1)
            if not name or name == "名称":
                continue
            scene_cell = csv_cell(cells, 6)
            conditions, specs = split_facility_notes(csv_cell(cells, 3))
            sheet_rows_parsed.append(
                {
                    "location_name": name,
                    "address": csv_cell(cells, 2),
                    "works_title": "",
                    "scene": abstract_scene(scene_cell) if scene_cell else "",
                    "accept_terms": conditions,
                    "condition": conditions,
                    "facility_spec": specs,
                    "detail_text": "",
                    "site_name": csv_cell(cells, 0) or "ロケ地",
                }
            )
        except Exception:
            continue
    master_rows_parsed: list[dict] = []
    for cells in master_table[1:]:
        try:
            name = csv_cell(cells, 0)
            if not name or name == "ロケ地名":
                continue
            label = csv_cell(cells, 3) or "汎用撮影"
            condition = csv_cell(cells, 2)
            master_rows_parsed.append(
                {
                    "location_name": name,
                    "address": csv_cell(cells, 1),
                    "accept_terms": condition,
                    "condition": condition,
                    "scene": label,
                    "scenes_category": label,
                    "works_title": "",
                    "detail_text": "",
                    "facility_spec": "",
                    "site_name": csv_cell(cells, 4) or "ロケ地",
                }
            )
        except Exception:
            continue
    if sheet_rows_parsed or master_rows_parsed:
        SHEET_CACHE["rows"] = sheet_rows_parsed
        SHEET_CACHE["at"] = now
        MASTER_CACHE["rows"] = master_rows_parsed
        MASTER_CACHE["at"] = now


def master_location_rows() -> list[dict]:
    try:
        refresh_csv_bundle()
    except Exception:
        pass
    return list(MASTER_CACHE.get("rows") or [])


def search_master_locations(query: str, limit: int = 0) -> list[dict]:
    scene_terms = scene_search_terms(query)
    expanded = [] if scene_terms else category_terms(query)
    terms = scene_terms or sheet_keywords(query)
    if not terms and not expanded:
        return []
    school = mentions_category(query, "学校")
    ranked = []
    for row in master_location_rows():
        if not acceptable_public_row(row):
            continue
        name = str(row.get("location_name") or "")
        address = str(row.get("address") or "")
        blob = "".join(str(row.get(key) or "") for key in ("location_name", "address", "scene", "scenes_category", "site_name"))
        if expanded:
            if not any(term in name for term in expanded):
                continue
            if school and any(noise in name for noise in ("公園", "道路", "通り", "交差点", "歩道橋")):
                continue
            score = sum(2 for term in expanded if term in name)
        else:
            if not any(term in blob for term in terms):
                continue
            score = sum(2 for term in terms if term in blob)
        if "東京" in address:
            score += 3
        ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    found = [row for _, row in ranked]
    return found if limit <= 0 else found[:limit]


def listed_key(row: dict) -> str:
    name = re.sub(r"\s+", "", str(row.get("location_name") or ""))
    address = re.sub(r"\s+", "", format_address(str(row.get("address") or "")))
    return f"{name}|{address}"


def unique_listed(rows: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for row in rows:
        key = listed_key(row)
        if key in {"|", ""} or key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def gather_location_rows(query: str, limit: int = 0, exclude_keys: set[str] | None = None) -> list[dict]:
    pool: list[dict] = []
    try:
        pool.extend(search_sheet_keywords(query, limit=0))
    except Exception:
        pass
    try:
        pool.extend(search_master_locations(query, limit=0))
    except Exception:
        pass
    found = prefer_tokyo(unique_listed(pool))
    if exclude_keys:
        found = [row for row in found if location_key(row) not in exclude_keys]
    if is_school_query(query):
        found = [row for row in found if acceptable_school_row(row)]
    if int(limit or 0) > 0:
        return found[: int(limit)]
    return found


def suggest_location_rows(user_text: str, existing: list[dict], messages: list[dict] | None) -> list[dict]:
    if len(existing) >= 10:
        return []
    known = "、".join(str(row.get("location_name") or "") for row in existing[:20] if row.get("location_name"))
    task = (
        "東京を中心に、関東（東京・神奈川・埼玉・千葉・茨城・栃木・群馬）の実在施設を10件以上出す。"
        "1〜3件で終わらせない。候補が無いという文は書かない。"
        "撮影日、作品名、URL、電話番号は書かない。個人の自宅は出さない。"
        "学校の質問は学校・廃校・校舎、オフィスや会社はビル・会議室・事務所、病院は医院・クリニック、家はハウススタジオ・古民家・撮影用の民家。"
        "1件につき1行。行頭は「- 」。"
        "形式は「- 施設名 / 市区町村 / 受入条件・特記（土日、夜間、搬入）」。"
        f"既出の名前は繰り返さない: {known or 'なし'}"
    )
    spoken = ask_gemini(user_text, messages or [], None, None, "", task)
    return parse_location_lines(spoken)


DETAIL_RE = re.compile(r"撮影条件|受入条件|条件は|条件を|どんなシーン|何のシーン|シーンで|詳しく|詳細|搬入|事前申請|夜間|土日")


def is_detail_question(text: str) -> bool:
    raw = text or ""
    if not DETAIL_RE.search(raw):
        return False
    if re.search(r"作り方|配合|合成|探して|候補|リスト", raw):
        return False
    compact = re.sub(r"\s+", "", raw)
    remembered: list[dict] = []
    try:
        remembered = list(st.session_state.get("last_listed_places") or [])
    except Exception:
        remembered = []
    if any(re.sub(r"\s+", "", str(row.get("location_name") or "")) in compact for row in remembered if len(str(row.get("location_name") or "")) >= 3):
        return True
    if re.search(r"この|その|さっき", raw):
        return True
    if re.search(r"詳しく|詳細", raw) and remembered and not re.search(r"学校|病院|オフィス|公園|スタジオ", raw):
        return True
    stripped = re.sub(
        r"撮影条件|受入条件|条件は|条件を|どんなシーン|何のシーン|シーンで撮影された|シーンで|詳しく|詳細|搬入|事前申請|夜間|土日祝|土日|教えて|ください|は|の|を|？|\?",
        "",
        compact,
    )
    for word in ("学校", "病院", "オフィス", "公園", "スタジオ", "カフェ", "ロケ地"):
        stripped = stripped.replace(word, "")
    return len(stripped) >= 2


def detail_places(user_text: str) -> list[dict]:
    compact = re.sub(r"\s+", "", user_text or "")
    remembered: list[dict] = []
    try:
        remembered = list(st.session_state.get("last_listed_places") or [])
    except Exception:
        remembered = []
    named = [row for row in remembered if re.sub(r"\s+", "", str(row.get("location_name") or "")) in compact]
    if named:
        return named[:3]
    found = gather_location_rows(user_text, limit=8)
    if found:
        return found[:3]
    return remembered[:1]


def detail_reply(user_text: str, messages: list[dict] | None = None) -> tuple[str, bool]:
    places = detail_places(user_text)
    facts = []
    for row in places:
        name, address, conditions, specs = public_facility(row)
        name = mask_public(name)
        scene = abstract_scene(mask_public(str(row.get("scene") or row.get("scenes_category") or "")))
        address = format_address(mask_public(address)) or "要確認"
        conditions = mask_public(conditions) or "要相談"
        specs = mask_public(specs)
        facts.append(
            "\n".join(
                part for part in (
                    f"施設名: {name}",
                    f"住所: {address}",
                    f"シーン: {scene}",
                    f"撮影受入条件: {conditions or '要相談'}",
                    f"搬入・設備: {specs}" if specs else "",
                ) if part
            )
        )
    spoken = ask_gemini(
        user_text,
        messages or [],
        None,
        None,
        "\n\n".join(facts) or "（この施設の台帳行はまだない）",
        "この質問の施設だけを、4文から6文で答える。JSONは書かない。"
        "撮影受入条件は土日祝、夜間、事前申請が分かれば書く。不明なら要相談。"
        "向いているシーンと搬入の要点を続ける。作品名、撮影日、俳優名、URL、電話番号は書かない。",
    )
    spoken = drop_empty_claims(spoken)
    if not spoken and facts:
        head = places[0]
        name = mask_public(str(head.get("location_name") or ""))
        address = format_address(mask_public(str(head.get("address") or ""))) or "要確認"
        scene = abstract_scene(mask_public(str(head.get("scene") or head.get("scenes_category") or "")))
        condition = mask_public(str(head.get("condition") or head.get("accept_terms") or "")) or "要相談"
        spoken = f"{name}（{address}）の撮影受入は{condition}です。向いているシーンは{scene}です。搬入や夜間の細目は、施設ごとの事前申請で確認します。"
    if not spoken:
        spoken = "撮影受入は要相談です。土日祝、夜間、搬入は、施設ごとの事前申請で確認します。"
    return f"<div class='bubble'>{rich_text(spoken)}</div>", True


def model_place_rows(text: str) -> list[dict]:
    raw = str(text or "")
    rows: list[dict] = []
    matched = re.search(r"\[\s*\{[\s\S]*\}\s*\]", raw)
    if matched:
        try:
            data = json.loads(matched.group(0))
        except Exception:
            data = []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or item.get("施設名") or item.get("location_name") or "").strip()
                address = str(item.get("address") or item.get("住所") or "").strip()
                scene = str(item.get("scene") or item.get("シーン") or "").strip()
                if name:
                    rows.append(
                        {
                            "location_name": name,
                            "address": address,
                            "scene": scene,
                            "scenes_category": scene,
                            "works_title": "",
                            "site_name": "kanto-public",
                        }
                    )
    if rows:
        return rows
    for line in raw.splitlines():
        body = re.sub(r"^[-・*]\s*", "", line.strip())
        found = re.match(r"(.+?)[:：](.+?)【(.+?)】", body)
        if not found:
            continue
        rows.append(
            {
                "location_name": found.group(1).strip(),
                "address": found.group(2).strip(),
                "scene": found.group(3).strip(),
                "scenes_category": found.group(3).strip(),
                "works_title": "",
                "site_name": "kanto-public",
            }
        )
    if rows:
        return rows
    return parse_location_lines(raw)


def gemini_location_rows(user_text: str, messages: list[dict] | None) -> list[dict]:
    import threading

    task = (
        "スプレッドシートが空だったときの補完。"
        "東京を優先し、関東（東京・神奈川・埼玉・千葉・茨城・栃木・群馬）の実在施設を10件以上、できるだけ多く書く。"
        "1件につき1行。形式は「・施設名：都道府県市区町村【シーン】」。"
        "JSON、作品名、撮影日、俳優名、URL、電話番号は書かない。個人の自宅は出さない。"
        "学校なら学校・廃校・校舎、病院なら医院・病棟、オフィスならビル・会議室。"
    )
    box = {"rows": []}

    def run() -> None:
        try:
            spoken = ask_gemini(user_text, messages or [], None, None, "", task)
            box["rows"] = model_place_rows(spoken)
        except Exception:
            box["rows"] = []

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(20)
    return list(box["rows"] or [])


RETRY_LIST_HTML = "<ul class='line-list'><li>・候補を読み直しています。施設名を添えて、もう一度送ってください。</li></ul>"
MAP_PROMO_HTML = (
    f'<a class="map-promo" href="{MAP_TOOL_URL}" target="_blank" rel="noopener noreferrer">'
    "💡 ロケ地図の作成・香盤整理には【AB式ロケ地図ツクール】が無料で使えます"
    "</a>"
)


def with_map_promo(body: str) -> str:
    if not body or body == RETRY_LIST_HTML or "line-list" not in body or "map-promo" in body:
        return body
    return body + MAP_PROMO_HTML


def location_reply(user_text: str, messages: list[dict] | None = None) -> tuple[str, bool]:
    try:
        st.session_state.last_match_count = None
    except Exception:
        pass
    try:
        if is_detail_question(user_text):
            reply, as_html = detail_reply(user_text, messages)
            if reply:
                return reply, as_html
            return f"<div class='bubble'>{rich_text('撮影受入は要相談です。土日祝、夜間、搬入は、施設ごとの事前申請で確認します。')}</div>", True
        more = is_more_request(user_text)
        query = followup_query(user_text) if more else user_text
        exclude = shown_location_keys(more)
        rows = gather_location_rows(query, limit=0, exclude_keys=exclude) if str(query or "").strip() else []
        listing = spot_cards_html(rows)
        if not listing:
            rows = gemini_location_rows(user_text, messages)
            listing = spot_cards_html(rows)
        try:
            st.session_state.last_match_count = len(rows) if listing and "読み直して" not in listing else 0
        except Exception:
            pass
        if rows and listing and "読み直して" not in listing:
            remember_locations(rows, query, more)
        if not listing:
            try:
                st.session_state.last_match_count = 0
            except Exception:
                pass
            return RETRY_LIST_HTML, True
        return with_map_promo(listing), True
    except Exception:
        try:
            st.session_state.last_match_count = 0
        except Exception:
            pass
        return RETRY_LIST_HTML, True


SHEET_CACHE: dict[str, object] = {"at": 0.0, "rows": []}


SPREADSHEET_ID = "1wqzKFN5GqvLjURzJNZ_ypNOC1jaYfYlVj6eDlAcUH44"
SHEET1_CSV = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid=0"
MASTER_CSV = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid=2043901308"
QUERY_SHEET_NAME = "未解決クエリ"
QUERY_HEADER = ["質問", "未解決", "フラグ", "件数", "記録日時", "状態"]
LOG_SHEET_NAME = "検索ログ"
LOG_HEADER = ["日時", "検索・相談テキスト", "検索ステータス"]


def open_master_book():
    raw = ""
    try:
        raw = str(st.secrets.get("GCP_SA_KEY", "") or "")
    except Exception:
        raw = ""
    raw = (raw or os.getenv("GCP_SA_KEY") or "").strip()
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if raw:
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    elif (BASE / "google-credentials.json").is_file():
        creds = Credentials.from_service_account_file(str(BASE / "google-credentials.json"), scopes=scopes)
    else:
        return None
    client = gspread.authorize(creds)
    try:
        return client.open_by_key(SPREADSHEET_ID)
    except Exception:
        return client.open("YUMI-CHA_ロケ地・お弁当マスター")


def open_master_worksheet():
    import gspread

    book = open_master_book()
    if book is None:
        return None
    try:
        return book.worksheet("シート1")
    except gspread.WorksheetNotFound:
        return book.sheet1


def ensure_query_sheet(book):
    import gspread

    try:
        worksheet = book.worksheet(QUERY_SHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = book.add_worksheet(title=QUERY_SHEET_NAME, rows=5000, cols=6)
        worksheet.append_row(QUERY_HEADER, value_input_option="RAW")
        return worksheet
    head = worksheet.row_values(1)
    if not head:
        worksheet.append_row(QUERY_HEADER, value_input_option="RAW")
    elif head[0] != "質問":
        worksheet.insert_row(QUERY_HEADER, index=1, value_input_option="RAW")
    return worksheet


def ensure_log_sheet(book):
    import gspread

    try:
        worksheet = book.worksheet(LOG_SHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = book.add_worksheet(title=LOG_SHEET_NAME, rows=5000, cols=3)
        worksheet.append_row(LOG_HEADER, value_input_option="RAW")
        return worksheet
    try:
        head = worksheet.row_values(1)
    except Exception:
        return worksheet
    if not head:
        try:
            worksheet.append_row(LOG_HEADER, value_input_option="RAW")
        except Exception:
            return worksheet
    elif head[0] != "日時":
        try:
            worksheet.insert_row(LOG_HEADER, index=1, value_input_option="RAW")
        except Exception:
            return worksheet
    return worksheet


def record_chat_query(query: str, reply: str) -> None:
    import threading
    import time

    text = str(query or "")
    if not text.strip():
        return
    visible = re.sub(r"<[^>]+>", "", reply or "")
    counted = None
    try:
        if "last_match_count" in st.session_state and st.session_state.get("last_match_count") is not None:
            counted = int(st.session_state.get("last_match_count") or 0)
    except Exception:
        counted = None
    unresolved = not visible.strip() or bool(EMPTY_HIT_RE.search(visible)) or looks_like_failure(visible)
    if counted == 0:
        unresolved = True
    if is_detail_question(text) and visible.strip() and not EMPTY_HIT_RE.search(visible):
        unresolved = False
    if counted is not None and counted > 0 and not unresolved:
        status = f"解決 / ヒット{counted}件"
    elif unresolved:
        status = "【未解決】"
    else:
        status = "解決"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_row = [stamp, text[:2000], status]
    unresolved_row = [
        text.strip()[:500],
        "true",
        "【未解決】",
        "0" if counted is None else str(counted),
        stamp,
        "open",
    ]

    def write_row() -> None:
        try:
            book = open_master_book()
            if book is None:
                return
            ensure_log_sheet(book).append_row(log_row, value_input_option="RAW")
            if status == "【未解決】":
                ensure_query_sheet(book).append_row(unresolved_row, value_input_option="RAW")
        except Exception:
            return

    try:
        threading.Thread(target=write_row, daemon=True).start()
    except Exception:
        return


def sheet_rows() -> list[dict]:
    try:
        refresh_csv_bundle()
    except Exception:
        pass
    return list(SHEET_CACHE.get("rows") or [])


def sheet_keywords(query: str) -> list[str]:
    try:
        terms = search_terms(query)
    except Exception:
        terms = []
    extra = re.findall(r"[一-龥ァ-ヶーA-Za-z0-9]{2,}", query or "")
    skip = {
        "とは", "誰です", "教えて", "ください", "について", "詳しく", "株式会社", "代表", "経歴", "実績",
        "撮影", "できる", "場所", "ロケ", "ロケ地", "候補", "お願い", "どこ", "ある", "ない", "シーン", "施設",
    }
    found = []
    for term in list(terms) + extra:
        if term in skip or len(term) < 2 or term in found:
            continue
        found.append(term)
    for word in category_terms(query):
        if word not in found:
            found.append(word)
    return found


def search_sheet_keywords(query: str, limit: int = 40) -> list[dict]:
    expanded = category_terms(query)
    terms = sheet_keywords(query)
    if not terms and not expanded:
        return []
    school = mentions_category(query, "学校")
    ranked = []
    for row in sheet_rows():
        if not acceptable_public_row(row):
            continue
        name = str(row.get("location_name") or "")
        address = str(row.get("address") or "")
        blob = "".join(
            str(row.get(key) or "")
            for key in ("location_name", "address", "accept_terms", "facility_spec", "detail_text", "scene", "site_name")
        )
        if is_bento_query(query) and not re.search(r"弁当|ケータリング|仕出し|ロケ弁", blob):
            continue
        if expanded:
            if not any(term in name for term in expanded):
                continue
            if school and any(noise in name for noise in ("公園", "道路", "通り", "交差点", "歩道橋")):
                continue
            score = sum(2 for term in expanded if term in name)
        else:
            score = sum(1 for term in terms if term in blob)
            if not score:
                continue
        if "東京" in address:
            score += 3
        elif any(area in address for area in ("神奈川", "千葉", "埼玉")):
            score += 2
        elif any(area in address for area in ("茨城", "栃木", "群馬")):
            score += 1
        ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    found = [row for _, row in ranked]
    return found if limit <= 0 else found[:limit]


def payload_value(payload: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, list):
            value = value[0] if value else ""
        text = str(value or "").strip()
        if text:
            return text
    return ""


def split_map_locator(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", ""
    if not re.match(r"https?://", text, re.I):
        cleaned = DATE_LEAK_RE.sub("", strip_confidential(text)).strip(" 、/")
        return cleaned, ""
    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    for key in ("address", "q", "query"):
        if not query.get(key):
            continue
        candidate = unquote(query[key][0]).replace("+", " ").strip()
        if re.match(r"^-?\d+(?:\.\d+)?\s*,\s*-?\d+", candidate):
            continue
        candidate = strip_confidential(DATE_LEAK_RE.sub(" ", candidate)).strip(" 、/")
        if candidate and not candidate.lower().startswith("http"):
            return candidate, text
    matched = re.search(r"/place/([^/]+)", parsed.path or "")
    if matched:
        place = unquote(matched.group(1)).replace("+", " ").strip()
        place = DATE_LEAK_RE.sub("", strip_confidential(place)).strip(" 、/")
        if place:
            return place, text
    return "", text


def scrub_map_url(url: str) -> str:
    text = str(url or "").strip()
    if not re.match(r"https?://", text, re.I):
        return "map-tool"
    parsed = urlparse(text)
    query = parse_qs(parsed.query, keep_blank_values=True)
    kept = []
    for key, values in query.items():
        if re.search(r"date|日付|撮影", key, re.I):
            continue
        for value in values:
            decoded = unquote(value)
            if DATE_LEAK_RE.search(decoded) or re.search(r"\d{4}-\d{1,2}-\d{1,2}", decoded):
                continue
            kept.append((key, value))
    cleaned = parsed._replace(query=urlencode(kept, doseq=True)).geturl()
    if DATE_LEAK_RE.search(unquote(cleaned)):
        return "map-tool"
    return f"map-tool:{cleaned}"


def looks_like_address(text: str) -> bool:
    return bool(re.search(r"都|道|府|県|区|市|町|村|丁目|\d", text or ""))


def map_payload_items(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("records", "locations", "items", "data"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [payload]
    return []


def normalize_map_record(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None
    payload_value(payload, ("撮影日", "日付", "shoot_date", "shot_date", "filming_date", "date", "map_date"))
    name = DATE_LEAK_RE.sub("", strip_confidential(payload_value(payload, ("ロケ地名称", "施設名", "名称", "location_name", "name", "title", "map_name")))).strip(" 、/")
    locator = payload_value(payload, ("地図URL/住所", "map_place"))
    url = payload_value(payload, ("地図URL", "map_url", "mapUrl", "url"))
    address = payload_value(payload, ("住所", "所在地", "address", "map_address"))
    located, located_url = split_map_locator(locator)
    if located_url and not url:
        url = located_url
    if not address and located:
        address = located
    if url and not looks_like_address(address):
        url_text, _url = split_map_locator(url)
        if looks_like_address(url_text):
            address = url_text
        elif not name:
            name = url_text
    address = DATE_LEAK_RE.sub("", strip_confidential(address)).strip(" 、/")
    if re.match(r"https?://", address or "", re.I):
        address = ""
    room = payload_value(payload, ("支度部屋", "控室", "dressing_room", "green_room", "map_room"))
    parking = payload_value(payload, ("駐車場", "parking", "map_parking"))
    facility = payload_value(payload, ("支度部屋・駐車場", "設備", "facility", "specs", "map_facility"))
    notes = []
    if facility:
        notes.append(facility)
    conditions, specs = split_facility_notes("。".join(notes))
    forced = []
    for label, raw in (("支度部屋", room), ("駐車場", parking)):
        cleaned = DATE_LEAK_RE.sub("", strip_confidential(raw)).strip(" 、/")
        if not cleaned or CREDIT_RE.search(cleaned):
            continue
        forced.append(cleaned if label in cleaned else f"{label}: {cleaned}")
    if forced:
        specs = "、".join(dict.fromkeys(forced + ([specs] if specs else [])))[:180]
    if not name or blocked_place(name, address, "map-tool", specs):
        return None
    detail = " / ".join(
        part for part in (
            f"利用条件: {conditions}" if conditions else "",
            f"設備: {specs}" if specs else "",
        ) if part
    )
    safe_url = url if re.match(r"https?://", url or "", re.I) else ""
    record = {
        "location_name": name,
        "address": address,
        "works_title": "",
        "accept_terms": conditions,
        "facility_spec": specs,
        "detail_text": detail,
        "source_url": scrub_map_url(safe_url),
        "site_name": "map-tool",
    }
    if not acceptable_public_row(record):
        return None
    return record


def upsert_learned_location(record: dict) -> bool:
    name = str(record.get("location_name") or "").strip()
    address = str(record.get("address") or "").strip()
    if not name:
        return False
    conditions = str(record.get("accept_terms") or "").strip()
    specs = str(record.get("facility_spec") or "").strip()
    source = str(record.get("source_url") or "map-tool")
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            """
            SELECT id, description, source_url FROM auto_learned_locations
            WHERE title = ? AND IFNULL(address, '') = ?
            ORDER BY id DESC LIMIT 1
            """,
            (name, address),
        ).fetchone()
        if row:
            old_cond, old_spec = split_facility_notes(str(row[1] or ""))
            conditions = "、".join(dict.fromkeys(part for part in (old_cond, conditions) if part))
            specs = "、".join(dict.fromkeys(part for part in (old_spec, specs) if part))
            description = " / ".join(
                part for part in (
                    f"利用条件: {conditions}" if conditions else "",
                    f"設備: {specs}" if specs else "",
                ) if part
            )
            kept_source = str(row[2] or "")
            if not kept_source.startswith("local-archive"):
                kept_source = source
            conn.execute(
                """
                UPDATE auto_learned_locations
                SET work_name = '', description = ?, source_url = ?
                WHERE id = ?
                """,
                (description, kept_source, row[0]),
            )
        else:
            description = str(record.get("detail_text") or "")
            conn.execute(
                """
                INSERT INTO auto_learned_locations (title, work_name, address, description, source_url)
                VALUES (?, '', ?, ?, ?)
                """,
                (name, address, description, source),
            )
        conn.commit()
        conn.close()
        record["accept_terms"] = conditions
        record["facility_spec"] = specs
        record["detail_text"] = description
        record["works_title"] = ""
        return True
    except Exception:
        return False


def upsert_master_sheet(record: dict) -> bool:
    import time

    name = str(record.get("location_name") or "").strip()
    if not name:
        return False
    try:
        worksheet = open_master_worksheet()
        if worksheet is None:
            return False
        values = worksheet.get_all_values()
        summary_cond, summary_spec = split_facility_notes(str(record.get("detail_text") or ""))
        conditions = "、".join(dict.fromkeys(part for part in (summary_cond, record.get("accept_terms") or "") if part))
        specs = "、".join(dict.fromkeys(part for part in (summary_spec, record.get("facility_spec") or "") if part))
        summary = " / ".join(
            part for part in (
                f"利用条件: {conditions}" if conditions else "",
                f"設備: {specs}" if specs else "",
            ) if part
        )
        for index, cells in enumerate(values[1:], start=2):
            current = str(cells[1] if len(cells) > 1 else "").strip()
            if current != name:
                continue
            current_address = str(cells[2] if len(cells) > 2 else "").strip()
            old_cond, old_spec = split_facility_notes(str(cells[3] if len(cells) > 3 else ""))
            conditions = "、".join(dict.fromkeys(part for part in (old_cond, conditions) if part))
            specs = "、".join(dict.fromkeys(part for part in (old_spec, specs) if part))
            summary = " / ".join(
                part for part in (
                    f"利用条件: {conditions}" if conditions else "",
                    f"設備: {specs}" if specs else "",
                ) if part
            )
            address = str(record.get("address") or "").strip() or current_address
            worksheet.update(
                range_name=f"C{index}:D{index}",
                values=[[address, summary]],
                value_input_option="RAW",
            )
            SHEET_CACHE["at"] = 0.0
            return True
        worksheet.append_row(
            [
                "ロケ地",
                name,
                str(record.get("address") or "").strip(),
                summary,
                str(record.get("source_url") or "map-tool"),
                time.strftime("%Y-%m-%d %H:%M"),
            ],
            value_input_option="RAW",
        )
        SHEET_CACHE["at"] = 0.0
        return True
    except Exception:
        return False


def merge_map_records(payload) -> list[dict]:
    merged = []
    for item in map_payload_items(payload):
        record = normalize_map_record(item)
        if not record:
            continue
        if not upsert_learned_location(record):
            continue
        upsert_master_sheet(record)
        merged.append(record)
    return merged


def ledger_fallback(user_text: str) -> tuple[str, bool]:
    if is_founder_query(user_text):
        return founder_card_html(), True
    if is_location_query(user_text):
        return location_reply(user_text)
    rows: list[dict] = []
    try:
        rows = search_sheet_keywords(user_text, limit=8)
    except Exception:
        rows = []
    if not rows:
        try:
            rows = search_local_locations(user_text, limit=8)
        except Exception:
            rows = []
    listing = spot_cards_html(rows)
    if listing:
        lead = "台帳から、言葉が一致したロケ地・お弁当の記録です。"
        return f"<div class='bubble'>{lead}</div>{listing}", True
    return "<div class='bubble'>台帳には、この言葉に一致する記録がまだありません。</div>", True


def produce_reply(user_text: str, messages: list[dict], image_bytes: bytes | None, mime: str | None) -> tuple[str, bool]:
    reply, as_html = "", False
    try:
        reply, as_html = compose_reply(user_text, messages, image_bytes, mime)
    except Exception:
        reply, as_html = "", False
    visible = re.sub(r"<[^>]+>", "", reply or "")
    if reply and is_location_query(user_text) and EMPTY_HIT_RE.search(visible):
        try:
            return location_reply(user_text, messages)
        except Exception:
            reply, as_html = "", False
    if reply:
        return reply, as_html
    kind = ""
    try:
        kind = craft_kind(user_text, messages)
    except Exception:
        kind = ""
    if kind:
        return craft_reply(kind, messages)
    try:
        return ledger_fallback(user_text)
    except Exception:
        if is_location_query(user_text) or is_scene_search(user_text):
            return RETRY_LIST_HTML, True
        return "<div class='bubble'>台帳には、この言葉に一致する記録がまだありません。</div>", True


def compose_reply(user_text: str, messages: list[dict], image_bytes: bytes | None, mime: str | None) -> tuple[str, bool]:
    from permit_lookup import is_permit_query, permit_answer

    bento = is_bento_query(user_text)
    if is_founder_query(user_text):
        spoken = ask_gemini(
            user_text,
            messages,
            image_bytes,
            mime,
            "",
            "代表YUMITA（弓田）と株式会社ALMONDBAGGERの公開情報だけを使い、経歴と実績を3文以内で答える。電話番号は出さない。",
        )
        card = founder_card_html()
        reply = f"<div class='bubble'>{rich_text(spoken)}</div>{card}" if spoken else card
        row_id = log_search_query(user_text)
        save_log_reply(row_id, reply)
        return reply, True
    if bento:
        reply = bento_reply(user_text, messages)
        row_id = log_search_query(user_text)
        save_log_reply(row_id, reply)
        return reply, reply.startswith("<")
    craft = ""
    try:
        craft = craft_kind(user_text, messages)
    except Exception:
        craft = ""
    if craft:
        reply, as_html = craft_reply(craft, messages)
        row_id = log_search_query(user_text)
        save_log_reply(row_id, reply)
        return reply, as_html
    if is_detail_question(user_text):
        reply, as_html = detail_reply(user_text, messages)
        row_id = log_search_query(user_text)
        save_log_reply(row_id, reply)
        return reply, as_html
    if is_scene_search(user_text):
        reply, as_html = location_reply(user_text, messages)
        row_id = log_search_query(user_text)
        save_log_reply(row_id, reply)
        return reply, as_html
    if is_desk_query(user_text):
        reply, as_html = desk_reply(user_text, messages)
        row_id = log_search_query(user_text)
        save_log_reply(row_id, reply)
        return reply, as_html
    if is_location_query(user_text):
        reply, as_html = location_reply(user_text, messages)
        row_id = log_search_query(user_text)
        save_log_reply(row_id, reply)
        return reply, as_html
    permit_text = ""
    try:
        if is_permit_query(user_text):
            permit_text = redact_private(permit_answer(user_text) or "")
    except Exception:
        permit_text = ""
    reply = conversation_reply(user_text, messages, image_bytes, mime, extra=permit_text)
    row_id = log_search_query(user_text)
    save_log_reply(row_id, reply)
    return reply, False


def apply_theme():
    background = file_b64(CIRCUIT_PATH)
    css = """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@700;800&family=Noto+Sans+JP:wght@400;500;700&display=swap');
    html, body, [class*="css"] { font-family: 'Noto Sans JP', sans-serif; }
    header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], footer { display: none !important; }
    [data-testid="stException"], [data-testid="stNotification"], .stException, .stAlert { display: none !important; }
    .stApp {
        background: #f4f7fa url("data:image/svg+xml;base64,%%BG%%") center / cover no-repeat fixed;
        color: #1e293b;
    }
    [data-testid="stSidebar"] {
        background: #2c3036 !important;
        color: #f8fafc !important;
    }
    [data-testid="stSidebar"][aria-expanded="true"] {
        width: 270px !important;
        min-width: 270px !important;
        max-width: 270px !important;
    }
    [data-testid="stSidebarHeader"] {
        position: absolute !important;
        top: 46% !important;
        right: 0 !important;
        left: auto !important;
        height: auto !important;
        background: transparent !important;
        z-index: 6;
    }
    [data-testid="stSidebarCollapseButton"] button {
        color: #ffffff !important;
        background: transparent !important;
    }
    [data-testid="stSidebarUserContent"] {
        padding: 0 0 48px 0 !important;
    }
    .st-key-brand {
        background: transparent;
        padding: 16px 12px 6px;
        margin: 0;
    }
    .brand-badge {
        border-radius: 16px;
        padding: 16px 10px 13px;
        text-align: center;
        background:
            radial-gradient(120% 80% at 0% 0%, rgba(56, 189, 248, 0.22), transparent 55%),
            radial-gradient(120% 90% at 100% 100%, rgba(196, 22, 28, 0.28), transparent 50%),
            linear-gradient(165deg, rgba(15, 23, 42, 0.96), rgba(44, 12, 20, 0.92));
        border: 1px solid rgba(56, 189, 248, 0.5);
        box-shadow: 0 0 18px rgba(56, 189, 248, 0.28), inset 0 0 16px rgba(196, 22, 28, 0.2);
    }
    .brand-mark {
        font-family: Montserrat, 'Noto Sans JP', sans-serif;
        font-weight: 800;
        font-size: 26px;
        letter-spacing: 0.12em;
        line-height: 1;
        background: linear-gradient(100deg, #38bdf8 0%, #7dd3fc 28%, #fb7185 68%, #c4161c 100%);
        -webkit-background-clip: text;
        background-clip: text;
        color: transparent;
    }
    .brand-sub {
        margin-top: 8px;
        color: rgba(226, 232, 240, 0.86);
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.08em;
    }
    .st-key-nav { padding: 6px 12px 0; }
    .st-key-nav button {
        display: flex !important;
        justify-content: flex-start !important;
        gap: 10px !important;
        width: 100% !important;
        text-align: left !important;
        border: none !important;
        box-shadow: none !important;
        border-radius: 12px !important;
        padding: 11px 14px !important;
        font-size: 15px !important;
        font-weight: 650 !important;
        color: #f8fafc !important;
    }
    .st-key-nav button[data-testid="stBaseButton-primary"],
    .st-key-nav button[kind="primary"] {
        background: #1a1d21 !important;
    }
    .st-key-nav button[data-testid="stBaseButton-tertiary"],
    .st-key-nav button[kind="tertiary"] {
        background: transparent !important;
    }
    .st-key-nav button:hover { background: rgba(255,255,255,0.06) !important; }
    .side-links { display: flex; flex-direction: column; gap: 8px; padding: 12px 12px 4px; }
    .cyber-link-btn {
        display: flex; align-items: center; justify-content: center; text-align: center;
        color: #f8fafc !important; text-decoration: none !important;
        border: 1px solid rgba(56, 189, 248, 0.4); border-radius: 12px;
        padding: 10px 12px; font-weight: 700; font-size: 12px; line-height: 1.35;
        background: rgba(15, 23, 42, 0.35);
        letter-spacing: 0.01em;
    }
    .cyber-link-btn:hover { border-color: #38bdf8; color: #38bdf8 !important; }
    .cyber-link-btn.artist {
        border-color: rgba(232, 184, 109, 0.55);
        background: linear-gradient(155deg, rgba(15, 23, 42, 0.72), rgba(88, 28, 48, 0.45));
        box-shadow: inset 0 0 0 1px rgba(232, 184, 109, 0.12), 0 0 16px rgba(196, 22, 28, 0.16);
    }
    .cyber-link-btn.artist:hover { border-color: #e8b86d; color: #f6e2bc !important; }
    .cyber-link-btn.youtube {
        border-color: rgba(248, 113, 113, 0.65);
        background: linear-gradient(155deg, rgba(69, 10, 10, 0.72), rgba(127, 29, 29, 0.55));
        box-shadow: inset 0 0 0 1px rgba(248, 113, 113, 0.16), 0 0 16px rgba(196, 22, 28, 0.2);
    }
    .cyber-link-btn.youtube:hover { border-color: #f87171; color: #fecaca !important; }
    .cyber-link-btn.report {
        border-color: rgba(246, 226, 188, 0.45);
        background: rgba(196, 22, 28, 0.22);
    }
    .cyber-link-btn.report:hover { border-color: #f6e2bc; color: #f6e2bc !important; }
    .st-key-reset { padding: 4px 12px 0; }
    .st-key-reset button {
        width: 100% !important; background: transparent !important; color: #f8fafc !important;
        border: 1px solid rgba(255,255,255,0.18) !important; border-radius: 12px !important;
        box-shadow: none !important;
    }
    .sponsor-slot {
        margin: 14px 12px 8px; border-radius: 16px; padding: 14px 14px 12px; text-align: left;
        border: 1px solid rgba(148, 163, 184, 0.28);
        background: rgba(15, 23, 42, 0.45);
        backdrop-filter: blur(12px);
        box-shadow: none;
    }
    .pr-badge {
        display: inline-block; font-size: 9px; letter-spacing: 0.18em; font-weight: 800;
        color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.4);
        border-radius: 999px; padding: 2px 6px;
    }
    .sponsor-body { margin-top: 8px; color: #cbd5e1; font-size: 12px; font-weight: 500; }
    .sponsor-slot.main {
        max-width: 760px; margin: 22px auto 8px;
        background: rgba(255, 255, 255, 0.62);
        border-color: rgba(148, 163, 184, 0.28);
        box-shadow: 0 8px 24px rgba(15, 23, 42, 0.04);
    }
    .sponsor-slot.main .sponsor-body { color: #64748b; }
    .block-container {
        max-width: 980px;
        padding-top: 1.2rem !important;
        padding-bottom: 120px !important;
    }
    .brand-title {
        text-align: center;
        font-family: Montserrat, 'Noto Sans JP', sans-serif;
        font-weight: 800;
        font-size: clamp(52px, 6.6vw, 86px);
        letter-spacing: 0.035em;
        line-height: 1;
        margin: 28px 0 22px;
    }
    .brand-title.compact { font-size: 42px; margin: 4px 0 12px; }
    .brand-title .yumi { color: #c4161c; }
    .brand-title .cha { color: #2b3036; }
    .msg-ai, .msg-user { display: flex; gap: 12px; margin: 0 auto 16px; max-width: 760px; }
    .msg-ai { align-items: flex-start; }
    .msg-user { justify-content: flex-end; }
    .ava { width: 52px; height: 52px; border-radius: 50%; flex: none; box-shadow: 0 6px 16px rgba(176, 20, 36, 0.28); }
    .who { font-weight: 800; color: #1f2937; font-size: 14px; margin: 2px 0 6px; }
    .bubble {
        background: #ffffff;
        border-radius: 18px;
        padding: 14px 16px;
        color: #1f2937;
        line-height: 1.75;
        font-size: 15px;
        box-shadow: 0 12px 32px rgba(15, 23, 42, 0.08);
    }
    .msg-ai .bubble { max-width: 640px; }
    .msg-user .bubble { max-width: 78%; border: 1px solid #f3d5d8; }
    .catch {
        text-align: center;
        color: #c4161c;
        font-weight: 700;
        font-size: 18px;
        margin: 4px 0 6px;
    }
    .subcatch { text-align: center; color: #64748b; margin-bottom: 18px; }
    .ext-row { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; margin-top: 8px; }
    a.ext {
        display: inline-flex; align-items: center; justify-content: center;
        min-width: 240px; background: #fff; color: #1f2937 !important;
        text-decoration: none !important; border: 1px solid #e6ebf0; border-radius: 14px;
        padding: 14px 18px; font-weight: 700;
        box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05);
    }
    a.ext:hover { border-color: #c4161c; color: #c4161c !important; }
    .page-copy {
        text-align: right; color: #94a3b8; font-size: 12px;
        letter-spacing: 0.04em; margin: 8px 8px 0;
    }
    .chat-bridge {
        max-width: 760px; margin: 28px auto 8px; padding-top: 18px;
        border-top: 1px solid rgba(148, 163, 184, 0.45);
        color: #64748b; font-size: 13px; letter-spacing: 0.08em; font-weight: 700;
    }
    .loc-lead { margin: 0 0 12px; color: #334155; font-size: 14px; }
    .talk { margin: 0 0 14px; color: #1e293b; font-size: 0.98rem; line-height: 1.75; }
    .line-list { list-style: none; margin: 8px 0 0; padding: 0; color: #0f172a; font-size: 0.98rem; line-height: 1.75; }
    .line-list li { margin: 0 0 2px; }
    a.map-promo {
        display: block; margin: 12px 0 0; padding: 10px 14px; border-radius: 12px;
        background: #fff7ed; color: #9a3412 !important; text-decoration: none !important;
        border: 1px solid rgba(251, 146, 60, 0.45); font-size: 13px; line-height: 1.6; font-weight: 700;
    }
    a.map-promo:hover { border-color: #c4161c; color: #c4161c !important; }
    pre.craft-doc {
        white-space: pre-wrap; word-break: break-word; margin: 10px 0 0;
        background: #f8fafc; color: #0f172a; border: 1px solid #e2e8f0; border-radius: 12px;
        padding: 14px 16px; font-size: 13px; line-height: 1.75;
        font-family: "Hiragino Sans", "Noto Sans JP", sans-serif;
    }
    .share-row { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; margin: 14px 0 4px; }
    a.share-btn {
        display: inline-flex; align-items: center; justify-content: center;
        min-width: 132px; padding: 8px 14px; border-radius: 999px;
        text-decoration: none !important; font-size: 13px; font-weight: 800;
        border: 1px solid #e6ebf0; background: #fff; color: #1f2937 !important;
    }
    a.share-btn.line { color: #06c755 !important; border-color: rgba(6, 199, 85, 0.45); }
    a.share-btn.x { color: #0f172a !important; }
    a.share-btn:hover { border-color: #c4161c; }
    .spot-list { margin: 12px 0 0; display: flex; flex-direction: column; gap: 12px; }
    .spot {
        display: block; margin: 0; padding: 14px 16px; border-radius: 16px;
        background: #ffffff; border: 1px solid rgba(148, 163, 184, 0.35);
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05);
    }
    .spot h3 {
        display: flex; align-items: baseline; gap: 8px;
        margin: 0 0 8px; color: #0f172a; font-size: 1.2rem; font-weight: 800; line-height: 1.35;
    }
    .spot-no {
        flex: 0 0 auto; color: #ef4444; font-size: 1.05rem; font-weight: 800;
    }
    .spot-addr, .spot-scene, .spot-feature, .spot-cond {
        display: block; margin: 4px 0 0 1.7rem; color: #334155; font-size: 0.92rem; line-height: 1.55;
    }
    .spot-addr { color: #64748b; }
    .spot-badges { margin: 8px 0 0; display: flex; flex-wrap: wrap; gap: 6px; }
    .spot-badge {
        display: inline-block; padding: 3px 8px; border-radius: 999px;
        background: #f1f5f9; color: #334155; font-size: 0.78rem; line-height: 1.45;
    }
    .spot-memo { margin-top: 8px; }
    .spot-memo summary { cursor: pointer; color: #64748b; font-size: 0.8rem; font-weight: 700; }
    .spot-memo p { margin: 6px 0 0; color: #334155; font-size: 0.86rem; line-height: 1.6; }
    .copy-block {
        margin: 10px 0 4px; padding: 12px 14px; border-radius: 12px;
        background: #0f172a; color: #f8fafc; white-space: pre-wrap;
        font-size: 0.88rem; line-height: 1.65; user-select: text;
    }
    .loc-cards { display: flex; flex-direction: column; gap: 12px; }
    .loc-card {
        background: linear-gradient(165deg, rgba(255,255,255,0.97), rgba(241,245,249,0.94));
        border: 1px solid rgba(56, 189, 248, 0.45);
        border-radius: 16px;
        padding: 16px 18px 14px;
        box-shadow: 0 12px 32px rgba(15, 23, 42, 0.06), inset 0 0 0 1px rgba(255,255,255,0.7);
    }
    .loc-name {
        margin: 0 0 6px; font-size: 1.05rem; font-weight: 700;
        color: #0f172a; letter-spacing: 0.01em; line-height: 1.4;
    }
    .loc-address { margin: 0 0 8px; color: #64748b; font-size: 0.88rem; }
    .loc-meta { margin: 4px 0 0; color: #475569; font-size: 0.92rem; line-height: 1.6; }
    .loc-meta span {
        display: inline-block; min-width: 7.6em; margin-right: 8px;
        color: #b45309; font-weight: 700; font-size: 0.75rem; letter-spacing: 0.08em;
    }
    .loc-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .loc-list li {
        margin: 0; padding: 8px 12px; border-radius: 12px;
        background: rgba(255,255,255,0.94);
        border: 1px solid rgba(56, 189, 248, 0.35);
        color: #1e293b; font-size: 0.95rem; line-height: 1.45;
    }
    .loc-list strong { color: #0f172a; font-weight: 700; }
    .loc-work { color: #475569; }
    .bubble.cards-bubble { background: transparent; box-shadow: none; padding: 0; max-width: 760px; }
    .desk-card {
        margin-top: 12px; padding: 16px 16px 14px; border-radius: 16px;
        background: #ffffff; border: 1px solid rgba(196, 22, 28, 0.28);
        box-shadow: 0 10px 28px rgba(15, 23, 42, 0.06);
    }
    .desk-note { margin: 0 0 12px; color: #475569; font-size: 0.86rem; line-height: 1.7; }
    .desk-card h3 { margin: 0 0 8px; font-size: 1rem; color: #0f172a; line-height: 1.45; }
    .desk-card ul { margin: 0 0 12px; padding-left: 1.15em; color: #1e293b; font-size: 0.92rem; line-height: 1.7; }
    .desk-actions { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; }
    .desk-actions a {
        display: inline-flex; align-items: center; border-radius: 999px;
        padding: 8px 14px; text-decoration: none; font-weight: 700; font-size: 0.86rem;
    }
    .desk-mail { background: #c4161c; color: #ffffff !important; }
    .desk-web { background: #f8fafc; color: #0f172a !important; border: 1px solid #e2e8f0; }
    .founder-card {
        max-width: 680px;
        border-radius: 18px;
        padding: 20px 20px 16px;
        color: #f8fafc;
        background:
            radial-gradient(120% 80% at 0% 0%, rgba(56, 189, 248, 0.22), transparent 52%),
            radial-gradient(90% 80% at 100% 100%, rgba(196, 22, 28, 0.28), transparent 46%),
            linear-gradient(165deg, rgba(15, 23, 42, 0.96), rgba(44, 12, 20, 0.94));
        border: 1px solid rgba(56, 189, 248, 0.5);
        box-shadow: 0 0 22px rgba(56, 189, 248, 0.22), 0 16px 36px rgba(15, 23, 42, 0.18);
    }
    .founder-kicker {
        margin: 0 0 8px;
        color: #7dd3fc;
        font-size: 11px;
        font-weight: 800;
        letter-spacing: 0.22em;
    }
    .founder-card h3 {
        margin: 0 0 8px;
        font-size: 1.28rem;
        line-height: 1.4;
        background: linear-gradient(100deg, #7dd3fc 0%, #fb7185 62%, #c4161c 100%);
        -webkit-background-clip: text;
        background-clip: text;
        color: transparent;
    }
    .founder-joke { margin: 0 0 12px; color: #f6e2bc; font-size: 13px; }
    .founder-row { margin: 4px 0; color: #e2e8f0; line-height: 1.7; }
    .founder-row span, .founder-label {
        display: inline-block;
        min-width: 4.2em;
        margin-right: 8px;
        color: #38bdf8;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0.08em;
    }
    .founder-label { margin: 12px 0 6px; }
    .founder-card ul { margin: 0 0 14px; padding-left: 1.2em; color: #f8fafc; }
    .founder-card li { margin: 4px 0; line-height: 1.7; }
    .founder-links { display: flex; flex-direction: column; gap: 8px; }
    .founder-links a {
        display: flex; align-items: center; justify-content: center;
        text-decoration: none !important; color: #f8fafc !important;
        border: 1px solid rgba(56, 189, 248, 0.45); border-radius: 12px;
        padding: 10px 12px; font-weight: 700; font-size: 13px;
        background: rgba(15, 23, 42, 0.45);
    }
    .founder-links a.artist {
        border-color: rgba(232, 184, 109, 0.55);
        background: linear-gradient(155deg, rgba(15, 23, 42, 0.72), rgba(88, 28, 48, 0.45));
    }
    .founder-links a:hover { border-color: #38bdf8; color: #7dd3fc !important; }
    .st-key-suggest,
    .st-key-composer {
        position: fixed;
        z-index: 30;
        left: 50%;
        transform: translateX(-50%);
    }
    .st-key-suggest {
        z-index: 29;
        bottom: 96px;
        width: min(760px, calc(100vw - 40px));
    }
    .stApp:has([data-testid="stSidebar"][aria-expanded="true"]) .st-key-suggest,
    .stApp:has([data-testid="stSidebar"][aria-expanded="true"]) .st-key-composer {
        left: calc(50% + 135px);
        width: min(760px, calc(100vw - 310px));
    }
    .st-key-suggest [data-testid="stHorizontalBlock"] { gap: 8px !important; }
    .st-key-suggest button {
        border-radius: 999px !important;
        background: rgba(255, 255, 255, 0.78) !important;
        color: #0f172a !important;
        border: 1px solid rgba(148, 163, 184, 0.4) !important;
        font-weight: 700 !important;
        font-size: 12px !important;
        min-height: 40px !important;
        height: auto !important;
        white-space: normal !important;
        line-height: 1.35 !important;
        backdrop-filter: blur(12px);
        box-shadow: 0 8px 18px rgba(15, 23, 42, 0.06) !important;
    }
    .st-key-composer {
        bottom: 20px;
        width: min(760px, calc(100vw - 40px));
        margin: 0;
        padding: 8px 10px;
        border-radius: 999px;
        background: #ffffff;
        backdrop-filter: none;
        -webkit-backdrop-filter: none;
        border: 1px solid rgba(148, 163, 184, 0.45);
        box-shadow: 0 12px 36px rgba(15, 23, 42, 0.12);
    }
    .st-key-composer:focus-within {
        border-color: #ef4444;
        box-shadow: 0 12px 36px rgba(15, 23, 42, 0.12), 0 0 0 3px rgba(239, 68, 68, 0.22);
    }
    .st-key-composer [data-testid="stHorizontalBlock"] {
        align-items: center !important;
        gap: 6px !important;
    }
    .st-key-composer [data-testid="stVerticalBlock"],
    .st-key-composer [data-testid="stElementContainer"] {
        margin-bottom: 0 !important;
        gap: 0 !important;
    }
    .st-key-composer [data-testid="stForm"] {
        position: relative !important;
        display: block !important;
        border: none; padding: 0; background: transparent; overflow: visible;
    }
    .st-key-composer [data-testid="stForm"] [data-testid="stVerticalBlock"] {
        display: grid !important;
        grid-template-columns: 1fr;
        height: 52px;
        min-height: 52px;
    }
    .st-key-composer [data-testid="stForm"] [data-testid="stVerticalBlock"] > .stElementContainer {
        grid-area: 1 / 1;
        align-self: center;
        width: 100% !important;
        margin: 0 !important;
    }
    .st-key-composer [data-testid="stForm"] [data-testid="stVerticalBlock"] > .stElementContainer:last-child {
        justify-self: end;
        align-self: center;
        width: 84px !important;
        height: 36px !important;
        margin: 0 8px 0 0 !important;
        z-index: 5;
    }
    .st-key-composer [data-testid="stTextInput"] { margin-bottom: 0; }
    .st-key-composer [data-testid="stTextInput"] [data-testid="InputInstructions"] { display: none !important; }
    .st-key-composer [data-testid="stTextInput"] input {
        height: 52px; border-radius: 999px !important;
        border: 1px solid rgba(148, 163, 184, 0.45) !important;
        padding: 0 100px 0 20px !important;
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
        caret-color: #111827;
        box-shadow: none;
        font-size: 15px !important;
    }
    .st-key-composer [data-testid="stTextInput"] input::placeholder {
        color: #6b7280 !important;
        -webkit-text-fill-color: #6b7280 !important;
    }
    .st-key-composer [data-testid="stTextInput"] input:focus {
        background: #ffffff !important;
        background-color: #ffffff !important;
        border-color: #ef4444 !important;
        outline: 2px solid #ef4444 !important;
        box-shadow: 0 0 0 4px rgba(239, 68, 68, 0.18);
    }
    .st-key-composer [data-testid="stFormSubmitButton"] {
        position: relative !important;
        width: 76px !important;
        height: 36px !important;
        margin: 0 !important;
        z-index: 5;
    }
    .st-key-composer [data-testid="stFormSubmitButton"] button {
        width: 76px !important; min-width: 76px; height: 36px !important;
        min-height: 36px !important;
        border-radius: 999px !important; background: #c4161c !important; color: #fff !important;
        border: none !important; font-weight: 700 !important; padding: 0 14px !important;
        margin: 0 !important;
        transform: translateY(8px);
    }
    .st-key-composer iframe { height: 44px !important; border: none !important; background: transparent !important; }
    .st-key-image_pop [data-testid="stPopover"] > button,
    .st-key-play_cm button {
        width: 44px !important; height: 44px !important; min-height: 44px !important;
        border-radius: 50% !important; padding: 0 !important;
        border: 1px solid #e6ebf0 !important; background: #fff !important; color: #4b5563 !important;
        box-shadow: 0 6px 16px rgba(15, 23, 42, 0.06) !important;
        font-size: 0 !important;
    }
    .st-key-image_pop [data-testid="stPopover"] > button p,
    .st-key-play_cm button p { display: none !important; }
    .st-key-play_cm button {
        background: #c4161c !important; color: #fff !important; border: none !important;
    }
    .attach-note { color: #c4161c; font-size: 12px; font-weight: 700; margin: 0 0 6px 18px; }
    .st-key-cmvideo {
        width: min(760px, 100%);
        margin: 6px auto 18px;
    }
    .st-key-cmvideo iframe {
        display: block;
        width: 100% !important;
        border: 1px solid rgba(56, 189, 248, 0.55) !important;
        border-radius: 16px !important;
        box-shadow: 0 0 22px rgba(56, 189, 248, 0.28), 0 0 34px rgba(196, 22, 28, 0.14);
        background: #0b1220;
        overflow: hidden;
    }
    [data-testid="stChatInput"] { display: none; }
    @media (max-width: 768px) {
        .block-container { padding-left: 12px !important; padding-right: 12px !important; padding-bottom: 190px !important; }
        .catch { font-size: 15px !important; line-height: 1.55; }
        .ext-row { flex-direction: column; align-items: stretch; width: 100%; }
        a.ext { width: 100%; min-width: 0; box-sizing: border-box; }
        .st-key-suggest,
        .st-key-composer,
        .stApp:has([data-testid="stSidebar"][aria-expanded="true"]) .st-key-suggest,
        .stApp:has([data-testid="stSidebar"][aria-expanded="true"]) .st-key-composer {
            left: 10px !important;
            right: 10px !important;
            width: auto !important;
            max-width: none !important;
            transform: none !important;
        }
        .st-key-suggest {
            bottom: 148px;
            overflow-x: auto;
            overflow-y: hidden;
            -webkit-overflow-scrolling: touch;
        }
        .st-key-suggest [data-testid="stHorizontalBlock"] {
            flex-wrap: nowrap !important;
            width: max-content !important;
            min-width: 100%;
        }
        .st-key-suggest button { white-space: nowrap !important; width: auto !important; }
        .st-key-composer { bottom: 10px; border-radius: 22px; padding: 8px; }
        .st-key-composer [data-testid="stHorizontalBlock"] {
            display: flex !important;
            flex-wrap: wrap !important;
            width: 100% !important;
            gap: 8px !important;
        }
        .st-key-composer [data-testid="stHorizontalBlock"] > div:first-child {
            flex: 1 1 100% !important;
            width: 100% !important;
            max-width: 100% !important;
            min-width: 0 !important;
        }
        .st-key-composer [data-testid="stHorizontalBlock"] > div:not(:first-child) {
            flex: 0 0 48px !important;
            width: 48px !important;
            max-width: 48px !important;
            min-width: 48px !important;
        }
        .st-key-composer [data-testid="stTextInput"] input {
            width: 100% !important;
            font-size: 16px !important;
            padding: 0 92px 0 14px !important;
        }
    }
    </style>
    """
    st.markdown(css.replace("%%BG%%", background), unsafe_allow_html=True)


def render_messages(messages: list[dict], avatar_b64: str):
    avatar = f"data:image/png;base64,{avatar_b64}" if avatar_b64 else ""
    blocks = []
    for message in messages:
        if message.get("html"):
            body = redact_private(message["content"])
            bubble_class = "bubble cards-bubble"
        else:
            body = rich_text(message["content"])
            bubble_class = "bubble"
        if message["role"] == "assistant":
            image = f'<img class="ava" src="{avatar}" alt="YUMI-CHA">' if avatar else ""
            blocks.append(
                f'<div class="msg-ai">{image}<div><div class="who">YUMI-CHA</div>'
                f'<div class="{bubble_class}">{body}</div></div></div>'
            )
        else:
            blocks.append(f'<div class="msg-user"><div class="bubble">{body}</div></div>')
    st.markdown("".join(blocks), unsafe_allow_html=True)


def adsense_is_live(slot: str) -> bool:
    client = ADSENSE_CLIENT.strip()
    slot = slot.strip()
    return client.startswith("ca-pub-") and "X" not in client and slot.isdigit() and int(slot) > 0


def flagship_cards(layout: str) -> str:
    cards = []
    for tone, title, kicker, href in FLAGSHIPS:
        cards.append(
            f'<a class="flag {html.escape(tone)}" href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f'<span>{html.escape(kicker)}</span><strong>{html.escape(title)}</strong></a>'
        )
    return f'<div class="flags {html.escape(layout)}">' + "".join(cards) + "</div>"


def sidebar_ad_markup() -> str:
    client = html.escape(ADSENSE_CLIENT.strip(), quote=True)
    slot_id = html.escape(ADSENSE_SLOT_SIDE.strip(), quote=True)
    return f"""
    <style>
      html, body {{ margin: 0; width: 100%; height: 250px; overflow: hidden; background: transparent; font-family: "Hiragino Sans", "Noto Sans JP", sans-serif; }}
      .ad-hold {{
        position: relative; box-sizing: border-box; width: 100%; height: 250px; border-radius: 14px; overflow: hidden;
        border: 1px solid rgba(248, 113, 113, 0.35);
        background:
          radial-gradient(140px 80px at 0% 0%, rgba(196,22,28,.62), transparent 72%),
          radial-gradient(160px 90px at 100% 100%, rgba(56,189,248,.28), transparent 70%),
          linear-gradient(155deg, #0b1220 0%, #1e293b 55%, #3f1d2e 100%);
      }}
      .ad-placeholder {{
        position: absolute; inset: 0; z-index: 0;
        display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center;
        letter-spacing: 0.16em;
      }}
      .ad-placeholder span {{ color: #fecaca; font-size: 11px; font-weight: 800; }}
      .ad-placeholder strong {{
        margin-top: 8px; color: #fff7ed; font-size: 18px; letter-spacing: 0.2em;
        font-family: Montserrat, "Hiragino Sans", sans-serif;
      }}
      .ad-hold ins.adsbygoogle {{
        position: relative; z-index: 1; display: block; width: 100%; height: 250px; background: transparent;
      }}
    </style>
    <div class="ad-hold">
      <div class="ad-placeholder" aria-hidden="true"><span>スポンサー募集中</span><strong>ALMONDBAGGER</strong></div>
      <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={client}" crossorigin="anonymous"></script>
      <ins class="adsbygoogle"
           style="display:block;width:100%;height:250px"
           data-ad-client="{client}"
           data-ad-slot="{slot_id}"
           data-ad-format="auto"
           data-full-width-responsive="true"></ins>
      <script>
        try {{
          (adsbygoogle = window.adsbygoogle || []).push({{}});
        }} catch (err) {{}}
      </script>
    </div>
    """


def sponsor_markup(slot: str, variant: str) -> str:
    client = html.escape(ADSENSE_CLIENT.strip(), quote=True)
    layout = "stack" if variant == "side" else "row"
    return f"""
    <style>
      html, body {{ margin: 0; background: transparent; font-family: "Hiragino Sans", "Noto Sans JP", sans-serif; }}
      .pr-wrap {{ padding: 2px; }}
      .pr-kicker {{ display: inline-block; font-size: 9px; letter-spacing: 0.18em; font-weight: 800; color: #94a3b8; border: 1px solid rgba(148,163,184,.45); border-radius: 999px; padding: 2px 6px; }}
      .flags {{ display: flex; gap: 8px; margin-top: 8px; }}
      .flags.stack {{ flex-direction: column; }}
      .flags.row {{ flex-direction: row; }}
      a.flag {{
        display: block; flex: 1; text-decoration: none; color: #f8fafc; border-radius: 14px; padding: 12px 12px 11px;
        border: 1px solid rgba(248, 113, 113, 0.35);
        background:
          radial-gradient(120px 40px at 0% 0%, rgba(196,22,28,.55), transparent 70%),
          linear-gradient(145deg, #0b1220 0%, #1e293b 58%, #3f1d2e 100%);
        box-shadow: inset 0 0 0 1px rgba(255,255,255,.04), 0 8px 18px rgba(15,23,42,.18);
      }}
      a.flag span {{ display: block; font-size: 10px; letter-spacing: 0.14em; color: #fecaca; font-weight: 700; }}
      a.flag strong {{ display: block; margin-top: 4px; font-size: 13px; line-height: 1.35; }}
      a.flag.map strong {{ color: #fff7ed; }}
      a.flag.house strong {{ color: #e0f2fe; }}
      a.flag.akbb strong {{ color: #fde68a; }}
    </style>
    <div class="pr-wrap" data-ad-client="{client}">
      <span class="pr-kicker">PR</span>
      {flagship_cards(layout)}
    </div>
    """


def render_sponsor(slot: str, variant: str):
    if variant == "side" and adsense_is_live(slot):
        components.html(sidebar_ad_markup(), height=250, scrolling=False)
    height = 292 if variant == "side" else 132
    components.html(sponsor_markup(slot, variant), height=height, scrolling=False)


def share_links() -> tuple[str, str]:
    from urllib.parse import quote

    text = "制作部のロケ地検索 YUMI-CHA"
    line = "https://social-plugins.line.me/lineit/share?url=" + quote(SHARE_PAGE, safe="")
    tweet = "https://twitter.com/intent/tweet?url=" + quote(SHARE_PAGE, safe="") + "&text=" + quote(text, safe="")
    return line, tweet


def render_share():
    line, tweet = share_links()
    st.markdown(
        f'<div class="share-row">'
        f'<a class="share-btn line" href="{html.escape(line, quote=True)}" target="_blank" rel="noopener noreferrer">LINEでシェア</a>'
        f'<a class="share-btn x" href="{html.escape(tweet, quote=True)}" target="_blank" rel="noopener noreferrer">Xでシェア</a>'
        f"</div>",
        unsafe_allow_html=True,
    )
    components.html(
        f"""
        <style>
          html, body {{ margin: 0; background: transparent; font-family: "Hiragino Sans", "Noto Sans JP", sans-serif; }}
          button {{
            width: 100%; height: 36px; border-radius: 999px; border: 1px solid #e6ebf0;
            background: #fff; color: #1f2937; font-weight: 800; font-size: 13px; cursor: pointer;
          }}
        </style>
        <button id="copy" type="button">URLをコピー</button>
        <script>
          const page = {json.dumps(SHARE_PAGE)};
          document.getElementById("copy").addEventListener("click", async () => {{
            try {{
              await navigator.clipboard.writeText(page);
              document.getElementById("copy").textContent = "コピーしました";
            }} catch (err) {{
              document.getElementById("copy").textContent = page;
            }}
          }});
        </script>
        """,
        height=42,
    )


def reset_chat():
    st.session_state.messages = []
    st.session_state.need_reply = False
    st.session_state.pending_image = None
    st.session_state.outbound_image = None
    st.session_state.show_cm = False
    st.session_state.shown_locations = []
    st.session_state.last_location_query = ""
    st.session_state.upload_key = st.session_state.get("upload_key", 0) + 1


def toggle_cm():
    st.session_state.show_cm = not st.session_state.get("show_cm", False)


MAP_QUERY_KEYS = (
    "map_payload",
    "map_name",
    "map_date",
    "map_url",
    "map_address",
    "map_place",
    "map_room",
    "map_parking",
    "map_facility",
    "ロケ地名称",
    "撮影日",
    "地図URL",
    "住所",
    "地図URL/住所",
    "支度部屋",
    "駐車場",
    "支度部屋・駐車場",
)


def query_value(key: str) -> str:
    if key not in st.query_params:
        return ""
    value = st.query_params[key]
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def consume_map_payload():
    if not any(key in st.query_params for key in MAP_QUERY_KEYS):
        return
    payload = None
    raw = query_value("map_payload")
    if raw:
        try:
            payload = json.loads(raw)
        except Exception:
            payload = None
    named = query_value("map_name") or query_value("ロケ地名称")
    located = query_value("map_place") or query_value("地図URL/住所")
    if payload is None and (named or located or query_value("map_url") or query_value("地図URL")):
        payload = {
            "ロケ地名称": named,
            "撮影日": query_value("map_date") or query_value("撮影日"),
            "地図URL": query_value("map_url") or query_value("地図URL"),
            "住所": query_value("map_address") or query_value("住所"),
            "地図URL/住所": located,
            "支度部屋": query_value("map_room") or query_value("支度部屋"),
            "駐車場": query_value("map_parking") or query_value("駐車場"),
            "支度部屋・駐車場": query_value("map_facility") or query_value("支度部屋・駐車場"),
        }
    for key in MAP_QUERY_KEYS:
        if key in st.query_params:
            del st.query_params[key]
    if not payload:
        return
    merged = merge_map_records(payload)
    if not merged:
        return
    names = "、".join(record["location_name"] for record in merged[:8])
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": f"地図ツクールの施設情報を検索対象に追加しました。{names}",
            "html": False,
        }
    )
    st.rerun()


def consume_speech():
    if "speech" not in st.query_params:
        return
    spoken = st.query_params["speech"]
    if isinstance(spoken, list):
        spoken = spoken[0] if spoken else ""
    del st.query_params["speech"]
    spoken = str(spoken).strip()
    if not spoken:
        return
    st.session_state.messages.append({"role": "user", "content": spoken})
    st.session_state.need_reply = True
    st.rerun()


def render_sidebar():
    with st.sidebar:
        with st.container(key="brand"):
            st.markdown(
                """
                <div class="brand-badge">
                    <div class="brand-mark">YUMI-CHA</div>
                    <div class="brand-sub">// PRODUCTION BRAIN OS</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with st.container(key="nav"):
            st.button("総合表示", icon=":material/dashboard:", key="nav_overview", width="stretch", type="primary")
        st.markdown(
            """
            <div class="side-links">
                <a class="cyber-link-btn" href="https://www.almondbagger.com/" target="_blank" rel="noopener noreferrer">🌐 会社公式HP (ALMOND BAGGER)</a>
                <a class="cyber-link-btn" href="https://map.almondbagger.com/" target="_blank" rel="noopener noreferrer">🗺️ ロケ地図ツクール</a>
                <a class="cyber-link-btn artist" href="https://www.tunecore.co.jp/artists?id=1169004" target="_blank" rel="noopener noreferrer">🎵 TuneCore (YUMITA Official)</a>
                <a class="cyber-link-btn youtube" href="https://youtube.com/@akbb_a.i.kuchipakubandbrothers?si=pk7gAKZ_JMLz1q_2" target="_blank" rel="noopener noreferrer">🎬 弊社制作MV（YouTube）</a>
                <a class="cyber-link-btn report" href="mailto:info@almondbagger.com?subject=%E3%80%90YUMI-CHA%E6%94%B9%E5%96%84%E8%A6%81%E6%9C%9B%E3%80%91">📩 要望・不具合を報告</a>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.container(key="reset"):
            st.button("🧹 会話をリセット", key="reset_chat", width="stretch", on_click=reset_chat)
        render_share()
        render_sponsor(ADSENSE_SLOT_SIDE, "side")


SUGGESTIONS = (
    ("🏢 都内で撮影できるオフィス・会議室", "都内で撮影できるオフィス・会議室"),
    ("🍱 現場で人気のロケ弁・ケータリング", "現場で人気のロケ弁・ケータリング"),
    ("👤 YUMITAって誰？", "YUMITA（弓田）とは誰ですか？株式会社ALMONDBAGGERの代表・制作部としての経歴や実績について詳しく教えてください。"),
    ("💥 爆破・道路封鎖の特殊撮影相談", "爆破・道路封鎖の特殊撮影相談"),
)


def send_suggestion(text: str):
    st.session_state.messages.append({"role": "user", "content": text})
    st.session_state.need_reply = True


def render_suggestions():
    if st.session_state.get("messages"):
        return
    with st.container(key="suggest"):
        cols = st.columns(4, gap="small")
        for index, (column, (label, query)) in enumerate(zip(cols, SUGGESTIONS)):
            column.button(label, key=f"chip_{index}", on_click=send_suggestion, args=(query,), width="stretch")


def render_composer():
    render_suggestions()
    with st.container(key="composer"):
        if st.session_state.get("pending_image"):
            st.markdown('<div class="attach-note">画像を添付しています</div>', unsafe_allow_html=True)
        text_col, mic_col, image_col, play_col = st.columns([8.6, 0.7, 0.7, 0.7], vertical_alignment="center", gap="small")
        with text_col:
            with st.form("send_form", clear_on_submit=True, border=False):
                user_text = st.text_input("メッセージ", label_visibility="collapsed", placeholder="メッセージを入力")
                submitted = st.form_submit_button("送信")
        with mic_col:
            components.html(MIC_HTML, height=48)
        with image_col:
            with st.container(key="image_pop"):
                with st.popover("\u200b", icon=":material/image:", help="画像を添付"):
                    uploaded = st.file_uploader(
                        "現場の画像",
                        type=["png", "jpg", "jpeg", "webp"],
                        key=f"upload_{st.session_state.upload_key}",
                        label_visibility="collapsed",
                    )
                    if uploaded is not None:
                        st.session_state.pending_image = uploaded.getvalue()
                        st.session_state.pending_mime = uploaded.type or "image/png"
                        st.image(uploaded, width=180)
                    if st.session_state.get("pending_image") and st.button("添付を外す", key="clear_image"):
                        st.session_state.pending_image = None
                        st.session_state.upload_key += 1
                        st.rerun()
        with play_col:
            st.button("\u200b", icon=":material/play_arrow:", key="play_cm", on_click=toggle_cm, help="会社CM")
        if submitted and user_text.strip():
            content = user_text.strip()
            if st.session_state.get("pending_image"):
                content += "\n（画像を添付）"
                st.session_state.outbound_image = st.session_state.pending_image
                st.session_state.outbound_mime = st.session_state.get("pending_mime") or "image/png"
                st.session_state.pending_image = None
                st.session_state.upload_key += 1
            st.session_state.messages.append({"role": "user", "content": content})
            st.session_state.need_reply = True
            st.rerun()


def render_chat(avatar_b64: str):
    st.markdown('<div class="chat-bridge">相談</div>', unsafe_allow_html=True)
    render_messages(st.session_state.messages, avatar_b64)
    if st.session_state.need_reply:
        st.session_state.need_reply = False
        last_user = ""
        for message in reversed(st.session_state.messages):
            if message["role"] == "user":
                last_user = message["content"].replace("\n（画像を添付）", "")
                break
        image = st.session_state.pop("outbound_image", None)
        mime = st.session_state.pop("outbound_mime", None)
        try:
            st.session_state.last_match_count = None
        except Exception:
            pass
        with st.spinner("YUMI-CHAが20年の現場アーカイブを照合しています..."):
            try:
                reply, as_html = produce_reply(last_user, st.session_state.messages, image, mime)
            except Exception:
                try:
                    if is_founder_query(last_user):
                        reply, as_html = founder_card_html(), True
                    elif is_bento_query(last_user):
                        reply = bento_reply(last_user)
                        as_html = reply.startswith("<")
                    elif is_desk_query(last_user):
                        reply, as_html = desk_reply(last_user, st.session_state.messages)
                    elif is_location_query(last_user):
                        reply, as_html = location_reply(last_user)
                    else:
                        reply, as_html = "", False
                except Exception:
                    reply, as_html = "", False
            try:
                record_chat_query(last_user, reply)
            except Exception:
                pass
        if not reply and (is_location_query(last_user) or is_scene_search(last_user)):
            reply, as_html = RETRY_LIST_HTML, True
        if reply:
            st.session_state.messages.append({"role": "assistant", "content": reply, "html": as_html})
        st.rerun()
    render_composer()


def render_overview():
    st.markdown(
        '<div class="brand-title compact"><span class="yumi">YUMI</span><span class="cha">-CHA</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="catch">アーティストYUMITAの20年間の現場の汗、判断、知恵を、1秒で。</div>'
        '<div class="subcatch">Production Brain OS  /  almond bagger</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="ext-row">
            <a class="ext" href="https://www.almondbagger.com/" target="_blank" rel="noopener noreferrer">会社公式HP</a>
            <a class="ext" href="https://map.almondbagger.com/" target="_blank" rel="noopener noreferrer">ロケ地図ツクール</a>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main():
    st.set_page_config(page_title="YUMI-CHA", page_icon=str(AVATAR_PATH), layout="wide", initial_sidebar_state="expanded")
    init_system_tables()
    apply_theme()
    api_key = gemini_api_key()
    if "gemini_client" not in st.session_state:
        try:
            st.session_state.gemini_client = genai.Client(api_key=api_key) if api_key else None
        except Exception:
            st.session_state.gemini_client = None
    if "messages" not in st.session_state:
        st.session_state.messages = []
    st.session_state.setdefault("show_cm", False)
    st.session_state.setdefault("need_reply", False)
    st.session_state.setdefault("upload_key", 0)
    st.session_state.setdefault("pending_image", None)
    st.session_state.setdefault("shown_locations", [])
    st.session_state.setdefault("last_location_query", "")
    consume_map_payload()
    consume_speech()
    avatar_b64 = file_b64(AVATAR_PATH)
    render_sidebar()
    render_overview()
    render_chat(avatar_b64)
    render_sponsor(ADSENSE_SLOT_MAIN, "main")
    st.markdown('<div class="page-copy">© ALMONDBAGGER</div>', unsafe_allow_html=True)


try:
    main()
except Exception:
    pass
