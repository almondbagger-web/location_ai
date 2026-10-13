"""制作部マニュアルと、手書きお弁当リストの参照。"""

import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MANUAL_PATH = BASE_DIR / "guild_manuals.json"
CATALOG_PATH = BASE_DIR / "bento_catalog.json"

# 手書き「おいしいお弁当屋さん」「タカネット経由」からの転記。
# 電話番号は発注前に原本で確認する。
BENTO_SHOPS = [
    {
        "name": "喜庄",
        "kana": "きょう",
        "genre": "和食",
        "price_min": 810,
        "price_note": "お茶なし ¥810〜",
        "menus": "カツ煮、ホッケ、サバの塩焼き、鯖の西京焼、唐揚げ、青椒肉絲",
        "notes": "基本は2種類まで。人気店なので直近は埋まりやすい。先の予定は仮予約し、詳細は後日連絡がおすすめ。",
        "phones": "お店 03-5981-8331 / 携帯 090-7711-5366",
        "area": "",
        "lead": "早めの仮予約",
        "tags": ["和食", "魚", "人気"],
    },
    {
        "name": "biji",
        "kana": "ビジ",
        "genre": "アジア系",
        "price_min": 756,
        "price_note": "¥756〜。予算に合わせて相談可",
        "menus": "ガパオライス、カオマンガイ、ビビンバ、タイ風照り焼きチキン、豆タコライス、ガイヤーン、キーマカレー、チキン南蛮、鯖の塩麹焼、鯖南蛮タルタル",
        "notes": "どれも美味、とリストにある。",
        "phones": "ヒガシさん 090-8642-3831",
        "area": "",
        "lead": "予算相談可",
        "tags": ["アジア", "カレー", "相談可"],
    },
    {
        "name": "きびだんご",
        "kana": "きびだんご",
        "genre": "健康和食",
        "price_min": 756,
        "price_note": "¥756〜",
        "menus": "ハンバーグ（トマト・和風・オニオン）、甘辛照り焼きチキン、ポーク胡麻しゃぶ、牛カルビ焼肉、赤魚の西京焼き、サンマのみぞれ煮、チキントマト煮、アジ南蛮漬",
        "notes": "ご飯を選べる（炊き込み、雑穀米鶏そぼろ、ピラフ、雑穀米鮭）。HPあり。前日発注も空きがあれば可。大川発注表でも夕に名前がある。",
        "phones": "080-1131-3251",
        "area": "",
        "lead": "前日、空きがあれば可",
        "tags": ["和食", "健康", "HP", "前日"],
    },
    {
        "name": "越谷八十吉",
        "kana": "こしがや やそきち",
        "genre": "魚が美味しい和食",
        "price_min": 864,
        "price_note": "基本 ¥864。予算によっては相談可",
        "menus": "サバのマリネチ、サバの文化チ、アジの開き、しまほっけ、サーモンハラス、あこう鯛のマリネチ、まぐろハラモ、アジフライ、鶏南蛮、唐揚げ、トンカツ、青椒肉絲",
        "notes": "くるめし掲載。メニューが豊富。埼玉が拠点。大川発注表でも昼に名前がある。リストに電話番号の記載はない。",
        "phones": "",
        "area": "埼玉",
        "lead": "予算相談可",
        "tags": ["魚", "埼玉", "和食"],
    },
    {
        "name": "ロケ弁当 Hungry",
        "kana": "ハングリー",
        "genre": "ロケ弁当",
        "price_min": 756,
        "price_note": "¥756〜。配送料は無料",
        "menus": "メニューが多いのでHPを見る",
        "notes": "地方でもどこでも配達可、とリストにある。HPとインスタあり。",
        "phones": "080-8879-3013",
        "area": "地方も可",
        "lead": "遠方・地方向き",
        "tags": ["地方", "配送無料", "HP", "インスタ"],
    },
    {
        "name": "大阪マドラスカレー",
        "kana": "マドラス",
        "genre": "カレー",
        "price_min": None,
        "price_note": "予算は相談",
        "menus": "カレーは一種類で、カツ、しめじ、ホウレン草、揚げ茄子、チーズ、生卵などのトッピングを変える",
        "notes": "北村一輝さんのお店、とリストにある。",
        "phones": "池本さん 080-6555-4694",
        "area": "",
        "lead": "予算相談",
        "tags": ["カレー", "相談可"],
    },
    {
        "name": "LOCO-MOTION",
        "kana": "ロコモーション",
        "genre": "カレー",
        "price_min": 702,
        "price_note": "DONぶり ¥702・¥810。カレーとドライカレーは ¥864〜",
        "menus": "ご飯の上にドライカレー。トッピングで値段が変わる。カレー弁当とドライカレー弁当もあり",
        "notes": "HPあり。大川発注表の昼にも名前がある。",
        "phones": "03-5799-4813",
        "area": "",
        "lead": "",
        "tags": ["カレー", "HP", "丼"],
    },
    {
        "name": "スマイルデリ",
        "kana": "スマイルデリ",
        "genre": "和食",
        "price_min": 770,
        "price_note": "いつも頼んでいるのは ¥770 の二段弁当。一段にも変更可",
        "menus": "季節限定メニューあり",
        "notes": "前日でも発注できる可能性が高く、融通が利く。HPあり。",
        "phones": "080-4853-8991",
        "area": "",
        "lead": "前日でも可能性が高い",
        "tags": ["和食", "前日", "HP", "融通"],
    },
    {
        "name": "仔熊",
        "kana": "こぐま",
        "genre": "和食",
        "price_min": 756,
        "price_note": "¥756〜。予算も相談可",
        "menus": "サーモン（多めが定番）、豚の生姜焼き",
        "notes": "タカネット経由。",
        "phones": "タカネット タカスギさん 090-1404-5892",
        "area": "",
        "lead": "タカネット経由",
        "via": "タカネット",
        "tags": ["和食", "タカネット", "魚", "相談可"],
    },
    {
        "name": "コロンバキッチン",
        "kana": "コロンバ",
        "genre": "洋食",
        "price_min": 756,
        "price_note": "¥756〜",
        "menus": "ハンバーグ シャリアピンソース（看板）、サーモンムニエル、チキンソテーデミソース、青じそチキンガーリックソテー、メンチカツ、赤魚南蛮",
        "notes": "タカネット経由。コロンバといえばハンバーグ。",
        "phones": "タカネット タカスギさん 090-1404-5892",
        "area": "",
        "lead": "タカネット経由",
        "via": "タカネット",
        "tags": ["洋食", "タカネット", "ハンバーグ"],
    },
    {
        "name": "焼肉の神様",
        "kana": "やきにくのかみさま",
        "genre": "焼肉",
        "price_min": 756,
        "price_note": "¥756〜",
        "menus": "牛焼き、豚焼き、マグロ焼き",
        "notes": "タカネット経由。大川発注表にも同名の記載がある。",
        "phones": "タカネット タカスギさん 090-1404-5892",
        "area": "",
        "lead": "タカネット経由",
        "via": "タカネット",
        "tags": ["焼肉", "タカネット"],
    },
    {
        "name": "ねいパン",
        "kana": "ねいパン",
        "genre": "韓国",
        "price_min": 756,
        "price_note": "¥756〜",
        "menus": "プルコギ、ヤンニョムチキン、タットリタン、サバの韓国風煮付け、タラ、豚キムチ",
        "notes": "タカネット経由。",
        "phones": "タカネット タカスギさん 090-1404-5892",
        "area": "",
        "lead": "タカネット経由",
        "via": "タカネット",
        "tags": ["韓国", "タカネット"],
    },
    {
        "name": "パロロ",
        "kana": "パロロ",
        "genre": "中華",
        "price_min": 756,
        "price_note": "¥756〜。仕入れ側のメモに ¥864〜 ともある",
        "menus": "回鍋肉、玉子木耳炒め、八宝菜、鳥ピリ辛。八角が効いた本格中華",
        "notes": "タカネット経由。",
        "phones": "タカネット タカスギさん 090-1404-5892",
        "area": "",
        "lead": "タカネット経由",
        "via": "タカネット",
        "tags": ["中華", "タカネット"],
    },
    {
        "name": "アルコバレーノ",
        "kana": "アルコバレーノ",
        "genre": "パスタ",
        "price_min": None,
        "price_note": "金額の記載なし",
        "menus": "",
        "notes": "タカネット経由。リストには店名とパスタだけ。",
        "phones": "タカネット タカスギさん 090-1404-5892",
        "area": "",
        "lead": "タカネット経由",
        "via": "タカネット",
        "tags": ["パスタ", "タカネット"],
    },
    {
        "name": "築地ムカイ",
        "kana": "つきじむかい",
        "genre": "和食",
        "price_min": None,
        "price_note": "金額の記載なし",
        "menus": "",
        "notes": "タカネット経由。大川発注表の夕にも「タカネット築地ムカイ」とある。",
        "phones": "タカネット タカスギさん 090-1404-5892",
        "area": "築地",
        "lead": "タカネット経由",
        "via": "タカネット",
        "tags": ["和食", "タカネット", "築地"],
    },
    {
        "name": "輪っか",
        "kana": "わっか",
        "genre": "和食",
        "price_min": None,
        "price_note": "金額の記載なし",
        "menus": "",
        "notes": "タカネット経由。大川発注表の夕にも名前がある。",
        "phones": "タカネット タカスギさん 090-1404-5892",
        "area": "",
        "lead": "タカネット経由",
        "via": "タカネット",
        "tags": ["和食", "タカネット"],
    },
    {
        "name": "JAPABEN",
        "kana": "ジャパベン",
        "genre": "おしゃれ和・洋",
        "price_min": None,
        "price_note": "リストに金額の記載なし",
        "menus": "さんまの蒲焼き、ハンバーグ、チキンバジルマヨ、白身魚のレモンタルタル、ネギ塩ポーク、唐揚げ、豚しゃぶおろしポン酢、メンチカツ",
        "notes": "大手町・汐留あたりなら直接注文可。HPあり。",
        "phones": "",
        "area": "大手町・汐留",
        "lead": "大手町・汐留なら直接",
        "tags": ["和食", "洋食", "HP", "大手町", "汐留"],
    },
    {
        "name": "鳥久",
        "kana": "とりきゅう",
        "genre": "鳥料理",
        "price_min": 700,
        "price_note": "¥700〜、¥800〜",
        "menus": "特製、から揚げそぼろ、もじ焼",
        "notes": "蒲田近郊。人気店なので早めの連絡。16:00には営業が終わっている日があるので、電話は早い時間がよい。HPあり。",
        "phones": "03-3731-5888",
        "area": "蒲田",
        "lead": "早め。電話は早い時間",
        "tags": ["鳥", "蒲田", "HP", "人気"],
    },
    {
        "name": "鳥鮮",
        "kana": "とりせん",
        "genre": "鳥料理",
        "price_min": 700,
        "price_note": "¥700〜。予算によって相談可",
        "menus": "から揚げ、赤魚、サバ",
        "notes": "鳥久ののれん分け。青葉台。大川発注表の夕にも名前がある。",
        "phones": "090-3687-4129",
        "area": "青葉台",
        "lead": "予算相談可",
        "tags": ["鳥", "青葉台", "相談可"],
    },
    {
        "name": "焼肉屋さんのお弁当",
        "kana": "やきにくやさん",
        "genre": "焼肉・韓国料理",
        "price_min": 756,
        "price_note": "おすすめは ¥864〜。¥756〜 もある",
        "menus": "",
        "notes": "前日16:00まで発注可。配達範囲が広め。HPあり。リストに電話番号の記載はない。",
        "phones": "",
        "area": "配達範囲広め",
        "lead": "前日16:00まで",
        "tags": ["焼肉", "韓国", "HP", "前日"],
    },
    {
        "name": "出目金",
        "kana": "でめきん",
        "genre": "いろいろ",
        "price_min": 700,
        "price_note": "¥700〜。日替わりあり",
        "menus": "めん類のお弁当あり。予算に合わせてメニューを考えてくれる",
        "notes": "土日は休み。電話も不可。HPあり。",
        "phones": "",
        "area": "",
        "lead": "平日のみ。土日は電話不可",
        "tags": ["麺", "日替わり", "HP", "相談可"],
    },
    {
        "name": "おこわ米八",
        "kana": "おこわよねはち",
        "genre": "上品な和食",
        "price_min": 700,
        "price_note": "¥700代のお弁当あり",
        "menus": "おこわが5種類くらいから選べる",
        "notes": "3日前の17:00までに発注。HPあり。リストに電話番号の記載はない。",
        "phones": "",
        "area": "",
        "lead": "3日前 17:00",
        "tags": ["和食", "おこわ", "HP"],
    },
    {
        "name": "塚田農場",
        "kana": "つかだのうじょう",
        "genre": "和食",
        "price_min": 800,
        "price_note": "¥800〜",
        "menus": "チキン南蛮が有名",
        "notes": "2日前の18:00が締切。HPあり。大川発注表の夕にも名前がある。",
        "phones": "",
        "area": "",
        "lead": "2日前 18:00",
        "tags": ["和食", "HP", "チキン南蛮"],
    },
    {
        "name": "崎陽軒",
        "kana": "きようけん",
        "genre": "崎陽軒",
        "price_min": 660,
        "price_note": "シウマイ弁当 ¥860 / 季節の弁当 ¥730 / 横濱チャーハン弁当 ¥660 / 普通のチャーハン弁当 ¥890",
        "menus": "シウマイ弁当、季節の弁当、横濱チャーハン弁当",
        "notes": "2日前の18:00が締切。HPあり。",
        "phones": "",
        "area": "",
        "lead": "2日前 18:00",
        "tags": ["シウマイ", "チャーハン", "HP"],
    },
    {
        "name": "とんかつまい泉",
        "kana": "まいせん",
        "genre": "とんかつ",
        "price_min": None,
        "price_note": "少し予算オーバー、とリストにある",
        "menus": "",
        "notes": "前日11:00まで注文可。HPあり。880円台の通常発注には向きにくい。",
        "phones": "",
        "area": "",
        "lead": "前日 11:00",
        "tags": ["とんかつ", "HP", "予算オーバー"],
    },
    {
        "name": "たちばな",
        "kana": "たちばな",
        "genre": "定食・弁当",
        "price_min": None,
        "price_note": "予算に合わせて作ってくれる",
        "menus": "朝食はヤル気マンマン弁当。魚定食、角煮、ギョーザが有名。おすすめを聞くとメニュー外も出ることがある",
        "notes": "前日発注もいける。ツイッターとインスタ。リストに電話番号の記載はない。大川発注表にも名前がある。",
        "phones": "",
        "area": "",
        "lead": "前日可。予算相談可",
        "tags": ["朝食", "相談可", "前日", "インスタ"],
    },
    {
        "name": "お弁当ちとせ",
        "kana": "ちとせ",
        "genre": "上品和食",
        "price_min": 864,
        "price_note": "HPの三種は ¥1,080〜。二種に変更すると ¥864〜 で発注可",
        "menus": "鮭がしっとりしていて一番、とリストにある",
        "notes": "絶品なので特別な日向き。人気店。HPあり。通常の880円台より一段上。",
        "phones": "",
        "area": "",
        "lead": "特別な日。二種なら ¥864〜",
        "tags": ["和食", "HP", "人気", "特別"],
    },
]

ORDER_NOTE = """
作品ごとの予算の目安:
- 往生際の意味を知れ！: 朝500円（税込）、昼夜770円（税込）。実際の昼夜は640〜890円が多く、770円ちょうどはやまぶ、べんとう屋なすび。変更期限は店ごとに前日15:00、18:00、20:00、当日午前など違う。
- 大根組JMS: 864円と880円が多い。880円ぴったりは K-meals、KIMAGUREYA、さかした、キッチンエビセン、横浜めしや、たちばなのカメラテスト。1000円超は上振れ。
- ロマンティックキラー: 朝食はポパイ648円、たちばな600円。昼夕は700〜900円が中心。
- 大川さんの発注表: 税込756〜972円、864円が特に多い。支払いの書き方は請求書郵送、当日、メール、現金、持参。
個人宅の住所は入れていない。配達先は香盤と地図で別管理する。
"""

MEAL_RULES = """
食事手配の現場ルール（基礎編より）:
- 発注前に、配達時間、場所、数量、納品書、箸、お茶の有無を確認する。
- 人数は±10〜20%動くと先に伝え、キャンセルのリミットも共有する。
- 肉・魚・麺を週で散らし、業者の提案を拾う。
- 朝食は受け取り担当を決め、移動飯なら片手で食べられるもの。都内の移動朝食の定番としてマニュアルはポパイ、匠のがってん弁当を挙げている。地方の早朝は仕出しを事前調査し、無ければコンビニへ必要数を事前発注。
- 昼食は10:00〜10:30に納品準備。猛暑は冷やし麺やカレー、冬は豚汁やカレー。テント、椅子、手洗い動線を見る。
- 夕食は早出と遅出で時間をずらす。暗い食事場所にはライトを先に置く。
- 夜食・つなぎは、おにぎり、いなり、サンド、焼きそば、うどん。予約がなければ買い集める。
- アレルギー、宗教、ベジの代替を用意する。子役には小分けか家庭風。
- 食後は分別してゴミをまとめ、次の食数と到着時刻を確認する。残さない、残させない。
- 明日の指差し確認に「弁当の納品時間、発注数、配膳場所、食事場所、現場と支度場所の配分」を入れる。
"""


def load_manuals():
    if not MANUAL_PATH.exists():
        return []
    return json.loads(MANUAL_PATH.read_text(encoding="utf-8"))


MANUALS = load_manuals()


def _norm_name(name):
    return re.sub(r"[\s・\-ー]", "", (name or "")).lower()


def load_catalog():
    if not CATALOG_PATH.exists():
        return []
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _shop_prices(shop):
    prices = []
    for value in shop.get("prices") or []:
        if isinstance(value, int) and value not in prices:
            prices.append(value)
    price_min = shop.get("price_min")
    if isinstance(price_min, int) and price_min not in prices:
        prices.append(price_min)
    return prices


def _merge_field(base, extra, key):
    left = (base.get(key) or "").strip()
    right = (extra.get(key) or "").strip()
    if not right or right in left:
        return
    base[key] = f"{left} {right}".strip() if left else right


def all_shops():
    shops = [dict(shop) for shop in BENTO_SHOPS]
    by_name = {_norm_name(shop["name"]): shop for shop in shops}
    for shop in shops:
        kana = _norm_name(shop.get("kana") or "")
        if kana:
            by_name.setdefault(kana, shop)
    for extra in load_catalog():
        target_name = extra.get("merge_into") or extra.get("name")
        target = by_name.get(_norm_name(target_name))
        if target is None:
            target = {
                "name": extra.get("name") or "名称不明",
                "kana": extra.get("kana") or "",
                "genre": extra.get("genre") or "",
                "price_min": None,
                "price_note": extra.get("price_note") or "",
                "menus": extra.get("menus") or "",
                "notes": extra.get("notes") or "",
                "phones": extra.get("phones") or "",
                "area": extra.get("area") or "",
                "lead": extra.get("lead") or "",
                "tags": list(extra.get("tags") or []),
                "sources": list(extra.get("sources") or []),
                "prices": list(extra.get("prices") or []),
            }
            shops.append(target)
            by_name[_norm_name(target["name"])] = target
            kana = _norm_name(target.get("kana") or "")
            if kana:
                by_name.setdefault(kana, target)
            continue
        for key in ("menus", "notes", "phones", "lead", "area", "price_note", "kana"):
            _merge_field(target, extra, key)
        for tag in extra.get("tags") or []:
            if tag not in target.setdefault("tags", []):
                target["tags"].append(tag)
        for source in extra.get("sources") or []:
            if source not in target.setdefault("sources", []):
                target["sources"].append(source)
        prices = target.setdefault("prices", [])
        for price in extra.get("prices") or []:
            if price not in prices:
                prices.append(price)
        if target.get("price_min") is None and prices:
            target["price_min"] = min(prices)
    for shop in shops:
        prices = _shop_prices(shop)
        if prices and shop.get("price_min") is None:
            shop["price_min"] = min(prices)
        shop["prices"] = prices
    return shops


def _tokens(text):
    words = re.findall(r"[一-龥ァ-ヶーA-Za-z0-9]{2,}", text or "")
    stop = {"こと", "もの", "ため", "よう", "ください", "お願い", "教えて", "したい", "明日", "今日"}
    return [word for word in words if word not in stop]


def _target_yen(text):
    match = re.search(r"(\d{3,4})\s*円", text or "")
    return int(match.group(1)) if match else None


def bento_for_query(query, limit=6):
    text = query or ""
    yen = _target_yen(text)
    tokens = _tokens(text)
    ranked = []
    catalog = all_shops()
    for shop in catalog:
        haystack = " ".join(
            [
                shop["name"],
                shop.get("kana") or "",
                shop.get("genre") or "",
                shop.get("menus") or "",
                shop.get("notes") or "",
                shop.get("area") or "",
                shop.get("lead") or "",
                " ".join(shop.get("tags") or []),
                " ".join(shop.get("sources") or []),
            ]
        )
        score = 0
        for token in tokens:
            if token in haystack or token in shop["name"]:
                score += 3
        if any(word in text for word in ("弁当", "ロケ弁", "食事", "仕出し", "ケータリング")):
            score += 1
        if shop.get("area") and shop["area"] in text:
            score += 6
        for word in ("魚", "肉", "カレー", "和食", "中華", "韓国", "鳥", "朝食"):
            if word in text and word in haystack:
                score += 4
        prices = shop.get("prices") or []
        if yen and prices:
            gap = min(abs(price - yen) for price in prices)
            if gap == 0:
                score += 12
            elif gap <= 30:
                score += 10
            elif gap <= 80:
                score += 6
            elif gap <= 150:
                score += 3
            elif min(prices) > yen + 200:
                score -= 3
        elif yen and not prices and "相談" in (shop.get("price_note") or ""):
            score += 2
        elif yen and "予算オーバー" in shop.get("tags", []):
            score -= 4
        if "前日" in text and "前日" in haystack:
            score += 4
        if "地方" in text and "地方" in haystack:
            score += 5
        for needle in ("往生際", "大根組", "JMS", "ロマンティック", "愛を誓い", "朝霧", "群馬", "大阪"):
            if needle in text and needle in haystack:
                score += 8
        if score > 0:
            ranked.append((score, shop))
    if not ranked and any(word in text for word in ("弁当", "ロケ弁", "食事", "円")):
        ranked = [(1, shop) for shop in catalog if shop.get("price_min") and shop["price_min"] <= 900]
    ranked.sort(key=lambda item: (-item[0], item[1].get("price_min") or 9999, item[1]["name"]))
    return [shop for _score, shop in ranked[:limit]]


def manual_for_query(query, limit=4):
    tokens = [token for token in _tokens(query) if len(token) >= 2][:10]
    if not tokens:
        return []
    ranked = []
    for chunk in MANUALS:
        score = 0
        for token in tokens:
            if token in chunk["text"]:
                score += chunk["text"].count(token)
        if score:
            ranked.append((score, chunk))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _score, chunk in ranked[:limit]]


def _esc(text):
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _missing(value):
    value = re.sub(r"\s+", " ", value or "").strip()
    return value if value else "リストに記載なし"


def shop_order_fields(shop):
    prices = shop.get("prices") or []
    price_note = (shop.get("price_note") or "").strip()
    if not price_note and prices:
        price_note = " / ".join(f"¥{price:,}" for price in prices)
    area = (shop.get("area") or "").strip()
    if "番号" in area:
        area = ""
    notes = (shop.get("notes") or "").strip()
    minimum = ""
    found = re.search(r"最低[^。\n]{0,24}", notes)
    if found:
        minimum = found.group(0)
    deadline = (shop.get("lead") or "").strip()
    if deadline and not re.search(r"まで|締切|前日|当日|発注|変更", deadline):
        deadline = ""
    if not deadline:
        found = re.search(
            r"((?:前々日|前日|当日|\d+日前)[^。\n]{0,18}(?:まで|締切|変更|発注|注文))",
            notes,
        )
        deadline = found.group(0) if found else ""
    phone = (shop.get("phones") or "").strip()
    if phone:
        deadline = f"{deadline} / 連絡 {phone}".strip(" /")
    return {
        "価格帯": _missing(price_note),
        "配達エリア・最低注文個数": f"{_missing(area)} / {_missing(minimum)}",
        "注文締切": _missing(deadline),
        "おすすめメニュー": _missing(shop.get("menus")),
        "現場ウケのポイント": _missing(notes),
    }


def render_bento_html(shops):
    if not shops:
        return "<p class='quest-empty'>弁当リストに、この条件の店は見つからなかった。</p>"
    cards = ["<p class='quest-key'>弁当リストから近い店</p>"]
    for shop in shops:
        fields = shop_order_fields(shop)
        lines = [f"<div class='quest-name'>{_esc(shop['name'])}</div>"]
        for label, value in fields.items():
            lines.append(f"<div class='quest-line'><span>{_esc(label)}</span>{_esc(value)}</div>")
        cards.append("<article class='quest-card'>" + "".join(lines) + "</article>")
    cards.append("<p class='quest-key'>電話番号がある店は、発注前に原本でもう一度確認してください。</p>")
    return "".join(cards)


def render_manual_html(chunks):
    if not chunks:
        return ""
    cards = ["<p class='quest-key'>制作部マニュアルの該当ページ</p>"]
    for chunk in chunks:
        preview = re.sub(r"\s+", " ", chunk["text"])[:220]
        cards.append(
            "<article class='quest-card'>"
            f"<div class='quest-line'>{_esc(preview)}</div>"
            "</article>"
        )
    return "".join(cards)


def prompt_context(query, include_shops=False, include_manuals=False):
    parts = []
    shops = bento_for_query(query, limit=8) if include_shops else []
    if include_shops and shops:
        lines = ["【お弁当リスト。回答では店舗の中身だけを使い、出典名やURLは書かない】"]
        for shop in shops:
            fields = shop_order_fields(shop)
            lines.append(
                f"- 店舗名: {shop['name']} / 価格帯: {fields['価格帯']} / "
                f"配達エリア・最低注文個数: {fields['配達エリア・最低注文個数']} / "
                f"注文締切: {fields['注文締切']} / おすすめメニュー: {fields['おすすめメニュー']} / "
                f"現場ウケのポイント: {fields['現場ウケのポイント']}"
            )
        lines.append(ORDER_NOTE.strip())
        if include_manuals:
            lines.append(MEAL_RULES.strip())
        parts.append("\n".join(lines))
    chunks = []
    if include_manuals:
        chunks = manual_for_query(query, limit=4)
        if chunks:
            lines = ["【制作部マニュアルの該当抜粋。ここに無い手順を、マニュアルに書いてあったように作らない】"]
            for chunk in chunks:
                snippet = re.sub(r"\s+", " ", chunk["text"])[:700]
                lines.append(f"- {chunk['source']} p.{chunk['page']}: {snippet}")
            parts.append("\n".join(lines))
    support = fc_bento_lines(query) if include_shops else ""
    if support:
        parts.append(support)
    return "\n\n".join(parts), shops, chunks


def fc_bento_lines(query: str) -> str:
    path = BASE_DIR / "fc_bento_notes.json"
    if not path.exists():
        return ""
    try:
        notes = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    picked = []
    for note in notes:
        site = note.get("site_name") or ""
        prefecture = note.get("prefecture") or ""
        snippet = re.sub(r"\s+", " ", note.get("snippet") or "").strip()
        label = re.sub(r"フィルムコミッション|ロケーションサービス|ロケ情報|検索", "", site)
        if len(snippet) < 8:
            continue
        mentioned = prefecture and prefecture in (query or "")
        mentioned = mentioned or (len(label) >= 2 and label[:4] in (query or ""))
        if not mentioned:
            continue
        picked.append(f"- {site}（{prefecture}）: {snippet[:180]}")
        if len(picked) >= 6:
            break
    if not picked:
        return ""
    return "【フィルムコミッション窓口の食事記載。店名が無い文から店を作らない】\n" + "\n".join(picked)
