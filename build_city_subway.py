#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""都市地下鉄の終電/時刻表ビルダー（福岡以外・ODPT非対応都市用）。

- 駅リスト＋座標は OSM(Overpass) から自動取得（¥0）
- 時刻表は build_train.py の関数を再利用して Yahoo!路線情報 から取得（¥0・全国対応）
- CONFIGS に都市を足せば同じ手順で展開できる

使い方:
    python3 build_city_subway.py osaka          # 実際に取得してJSONを作る
    python3 build_city_subway.py tokyo --count   # 駅数だけ確認（Yahooには行かない）

出力: out/<slug>_subway_timetable.json（train_timetable.json と同じスキーマ）
"""
import json, os, re, sys, time, urllib.request

os.chdir(os.path.dirname(os.path.abspath(__file__)))
import build_train as bt  # fetch/find_station_id/get_lines_at_station/get_timetable/scrape_all を再利用

# ── 都市設定（ここに足せば他都市へ展開できる）──
# overpass_area: Overpassでの検索名。admin_level はデフォルト7（市区町村）。
#   東京23区のように「都」に対応する単一のadmin_level=7領域が無い都市は
#   overpass_admin_level / overpass_area_alt で個別に上書きする（§2.2の注意）。
# expected_stations: 目安駅数。実際の取得数がこれから大きく外れたら
#   範囲指定を疑うためのガード（指示書C §2.2）。
CONFIGS = {
    "sapporo": {
        "feed_label": "札幌（地下鉄）",  # 棚の索引に出す名前
        "pref": "北海道",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "札幌市",
        "slug": "sapporo",
        "overpass_area": "札幌市",
        "overpass_admin_level": "7",
        "line_patterns": {"subway": [r"札幌市営地下鉄"]},  # Yahoo表記に一致・JR等を除外
        "search_suffix": "札幌",                          # 同名駅対策（出力名はcleanなまま）
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 46,
    },
    "osaka": {
        "feed_label": "大阪・堺（地下鉄・JR・私鉄）",  # 棚の索引に出す名前
        "pref": "大阪府",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "大阪市・堺市",
        "slug": "osaka",
        # 2026-08-03: 政令市の堺を載せるため範囲を大阪市＋堺市にした
        # （運賃ブロック「大阪地区」＝大阪府全域なので営業圏としても同じ）。
        # 堺市からは地下鉄3駅（なかもず・北花田・新金岡）が入る。
        "overpass_wards": ["大阪市", "堺市"],  # どちらも全国で唯一の名前＝安全
        "overpass_admin_level": "7",
        # Yahoo実表記は「OsakaMetro御堂筋線」のようにスペース無し英字＋路線名
        # （実測確認済み。"大阪メトロ"/"Osaka Metro"では一致しない）。
        # 🔴 2026-08-03: 堺のターミナル（堺東＝南海高野線、堺市＝ＪＲ阪和線、
        #    浜寺駅前＝阪堺電気軌道）を取るため、ＪＲ・私鉄のパターンを足した。
        #    パターンは全駅に当てるので、**大阪市内の駅にもＪＲ・私鉄の時刻が
        #    乗るようになる**（京都・神戸・横浜と同じ形。乗り入れが無ければ
        #    一致しないだけで余計な取得は起きない）。
        "line_patterns": {
            "subway": [r"OsakaMetro", r"大阪メトロ", r"Osaka\s*Metro"],
            "jr": [r"ＪＲ"],
            "private": [r"南海", r"阪堺", r"近鉄", r"阪急", r"阪神", r"京阪"],
        },
        "terminals": [
            # 堺市（政令市・2026-08-03 追加）
            "堺東", "堺", "堺市", "中百舌鳥", "三国ヶ丘", "浜寺駅前", "鳳",
            "北野田", "深井", "泉ケ丘", "上野芝", "百舌鳥", "石津川",
            "諏訪ノ森", "浜寺公園", "初芝", "白鷺", "津久野",
        ],
        "search_suffix": "大阪",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 100,
    },
    "nagoya": {
        "feed_label": "名古屋（地下鉄）",  # 棚の索引に出す名前
        "pref": "愛知県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "名古屋市",
        "slug": "nagoya",
        "overpass_area": "名古屋市",
        "overpass_admin_level": "7",
        "line_patterns": {"subway": [r"名古屋市営地下鉄", r"名古屋市高速電気軌道"]},
        "search_suffix": "名古屋",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 87,
    },
    # 東京23区は「都」に対応する単一のadmin_level=7領域がOSMに無い（23区が
    # それぞれ市町村相当=admin_level7）。23区名を列挙してOR検索する。
    # ⚠️ 駅数目安280はここから大きく外れやすい＝必ず --count で先に確認すること。
    "tokyo": {
        "feed_label": "東京（地下鉄）",  # 棚の索引に出す名前
        "pref": "東京都",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "東京都",
        "slug": "tokyo",
        "overpass_wards": [
            "千代田区", "中央区", "港区", "新宿区", "文京区", "台東区", "墨田区",
            "江東区", "品川区", "目黒区", "大田区", "世田谷区", "渋谷区", "中野区",
            "杉並区", "豊島区", "北区", "荒川区", "板橋区", "練馬区", "足立区",
            "葛飾区", "江戸川区",
        ],
        "overpass_admin_level": "7",
        "line_patterns": {"subway": [r"東京メトロ", r"都営地下鉄"]},
        "search_suffix": "東京",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 280,
    },
    # ── ここから 2026-08-01 追加の4都市（地下鉄＋主要ターミナル駅）──
    # 路線名は「推測で書かない」（指示書C §2.2）。下の表記はすべて
    # 代表駅1駅をYahooに当てて実際に返ってきた文字列から起こしている。
    #   🔴 JRは半角 "JR" ではなく **全角「ＪＲ」**（ＪＲ京都線／ＪＲ神戸線…）。
    #      半角で書くと1路線も一致しない。
    "kyoto": {
        "feed_label": "京都（地下鉄・JR・私鉄）",  # 棚の索引に出す名前
        "pref": "京都府",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "京都市",
        "slug": "kyoto",
        "overpass_area": "京都市",
        "overpass_admin_level": "7",
        # 実測（烏丸御池）: 京都市営地下鉄烏丸線／京都市営地下鉄東西線
        # 実測（京都）: ＪＲ京都線・ＪＲ奈良線・ＪＲ嵯峨野線・ＪＲ湖西線・
        #               ＪＲ琵琶湖線・ＪＲ東海道新幹線・近鉄京都線／阪急京都本線
        "line_patterns": {
            "subway": [r"京都市営地下鉄"],
            "jr": [r"ＪＲ"],
            "private": [r"近鉄", r"阪急", r"京阪", r"叡山電鉄", r"嵐電", r"京福"],
        },
        "terminals": [
            "京都", "山科", "二条", "円町", "丹波口", "桂", "桂川", "西院",
            "京都河原町", "烏丸", "大宮", "出町柳", "三条", "東福寺",
            "伏見稲荷", "嵯峨嵐山", "丹波橋", "中書島", "六地蔵",
        ],
        "search_suffix": "京都",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 30,  # 実測30（六地蔵は宇治市なので京都市域に入らない）
    },
    "kobe": {
        "feed_label": "神戸（地下鉄・JR・私鉄）",  # 棚の索引に出す名前
        "pref": "兵庫県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "神戸市",
        "slug": "kobe",
        "overpass_area": "神戸市",
        "overpass_admin_level": "7",
        # 実測（三宮・花時計前）: 神戸市営地下鉄海岸線／（三ノ宮）: ＪＲ神戸線
        "line_patterns": {
            "subway": [r"神戸市営地下鉄", r"神戸市営北神線"],
            "jr": [r"ＪＲ"],
            "private": [r"阪急", r"阪神", r"山陽電鉄", r"神戸電鉄", r"神戸高速",
                        r"ポートライナー", r"ポートアイランド", r"六甲ライナー"],
        },
        "terminals": [
            "三ノ宮", "神戸三宮", "元町", "神戸", "新神戸", "六甲道", "灘",
            "兵庫", "須磨", "舞子", "板宿", "新開地", "高速神戸", "湊川",
            "谷上", "鈴蘭台", "垂水", "住吉", "西神中央",
        ],
        "search_suffix": "神戸",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 27,  # 実測27（西神・山手線＋海岸線＋北神線の谷上）
    },
    "yokohama": {
        "feed_label": "横浜・川崎（地下鉄・JR・私鉄）",  # 棚の索引に出す名前
        "pref": "神奈川県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "横浜市・川崎市",
        "slug": "yokohama",
        # 2026-08-03: 京浜交通圏は横浜市と川崎市の**2つの政令市**を含むのに、
        # 川崎市の駅が1つも入っていなかった。範囲を両市に広げる。
        # 川崎市に地下鉄は無いので、増えるのはターミナル駅だけ（目安駅数は据置き）。
        "overpass_wards": ["横浜市", "川崎市"],  # どちらも全国で唯一の名前＝安全
        "overpass_admin_level": "7",
        # 実測（関内）: 横浜市営地下鉄ブルーライン／ＪＲ根岸線
        # 実測（横浜）: みなとみらい線・京急本線・東急東横線・相鉄本線・ＪＲ各線
        "line_patterns": {
            "subway": [r"横浜市営地下鉄"],
            "jr": [r"ＪＲ"],
            # 🔴 川崎の 新百合ヶ丘・登戸＝小田急、京王稲田堤＝京王。この2つを
            #    足さないと川崎北部のターミナルが1駅も取れない。
            "private": [r"京急", r"京浜急行", r"東急", r"相鉄", r"みなとみらい線",
                        r"横浜シーサイドライン", r"金沢シーサイドライン",
                        r"小田急", r"京王"],
        },
        "terminals": [
            "横浜", "新横浜", "桜木町", "関内", "石川町", "東神奈川", "鶴見",
            "戸塚", "上大岡", "日吉", "菊名", "二俣川", "元町・中華街",
            "みなとみらい", "金沢文庫", "保土ケ谷", "港南台", "綱島", "新杉田",
            # 川崎市（政令市・2026-08-03 追加）
            "川崎", "京急川崎", "武蔵小杉", "溝の口", "武蔵溝ノ口", "登戸",
            "新百合ヶ丘", "鹿島田", "新川崎", "尻手", "川崎大師", "稲田堤",
            "京王稲田堤", "向ヶ丘遊園", "宮前平", "鷺沼", "元住吉", "浜川崎",
            "小田栄", "武蔵中原", "武蔵新城",
        ],
        "search_suffix": "横浜",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 39,  # 実測39（湘南台は藤沢市。共有駅を除いた横浜市域の数）
    },
    # 🔴 北九州には地下鉄が無い。主役はモノレール（13駅）なので、OSMから
    #    拾う時のタグも subway ではなく monorail にする（overpass_filters）。
    "kitakyushu": {
        "feed_label": "北九州（モノレール・JR）",  # 棚の索引に出す名前
        "pref": "福岡県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "北九州市",
        "slug": "kitakyushu",
        "overpass_area": "北九州市",
        "overpass_admin_level": "7",
        "overpass_filters": [
            '["railway"="station"]["station"="monorail"]',
            '["railway"="station"]["monorail"="yes"]',
        ],
        # 実測（小倉）: 北九州モノレール小倉線・ＪＲ山陽新幹線・ＪＲ鹿児島本線・
        #               ＪＲ日豊本線・ＪＲ日田彦山線
        "line_patterns": {
            "monorail": [r"北九州モノレール"],
            "jr": [r"ＪＲ"],
            "private": [r"筑豊電気鉄道", r"平成筑豊鉄道"],
        },
        "terminals": [
            "小倉", "西小倉", "戸畑", "八幡", "黒崎", "折尾", "門司", "門司港",
            "城野", "下曽根", "スペースワールド", "九州工大前", "若松", "二島",
            "朽網", "小森江", "陣原", "南小倉",
        ],
        "search_suffix": "北九州",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 13,  # 北九州モノレール小倉線
    },
    # ── ここから 2026-08-03 追加：政令指定都市の残り全部（社長指示）──
    #
    # 【考え方】その街の「主役の乗り物」＋主要ターミナル駅。地下鉄がある街は
    # 地下鉄、無い街はモノレール・新交通・路面電車が主役になる（北九州と同じ）。
    # 主役が何も無い街（新潟・相模原）は**ターミナル駅だけ**で作る。
    #
    # 🔴 line_patterns は全部、代表駅をYahooに当てて返ってきた実際の文字列から
    #    起こしている（推測で書くと1路線も一致しない）。特に:
    #      ・仙台は「仙台市**営**地下鉄」ではなく **「仙台市地下鉄」**
    #      ・熊本市電の系統名は全角の **Ａ／Ｂ**
    #      ・JRは全角「ＪＲ」（既知の罠）
    # 🔴 overpass_filters もOSMを実際に数えて決めている。新潟の路面電車タグは
    #    **バス営業所の誤タグが1件あるだけ**なので使わない（拾うと嘘の駅が出る）。
    "sendai": {
        "feed_label": "仙台（地下鉄・JR）",  # 棚の索引に出す名前
        "pref": "宮城県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "仙台市",
        "slug": "sendai",
        "overpass_area": "仙台市",
        "overpass_admin_level": "7",
        # 実測（長町南・泉中央）: 仙台市地下鉄南北線
        # 実測（仙台）: ＪＲ東北本線・仙山線・仙石線・常磐線・東北新幹線・
        #               秋田新幹線／仙台空港アクセス線
        "line_patterns": {
            "subway": [r"仙台市地下鉄"],
            "jr": [r"ＪＲ"],
            "private": [r"仙台空港アクセス線", r"仙台空港鉄道", r"阿武隈急行"],
        },
        "terminals": [
            "仙台", "あおば通", "長町", "北仙台", "東仙台", "南仙台", "太子堂",
            "岩切", "陸前原ノ町", "苦竹", "榴ケ岡", "小鶴新田", "福田町",
            "中野栄", "陸前落合", "愛子", "東照宮",
        ],
        "search_suffix": "仙台",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 29,  # 実測29（南北線＋東西線・仙台駅は共有で1つ）
    },
    # 🔴 広島は主役が2つ（アストラムライン＝新交通22駅／広島電鉄＝路面電車68停留場）。
    #    営業圏「広島市域地区」は廿日市市・府中町・海田町・熊野町・坂町も含むが、
    #    範囲は**広島市だけ**にしてある。overpass_wards は市区町村を名前で引くので、
    #    「府中町」「坂町」のような**全国にありふれた名前**を入れると別の県の
    #    同名自治体まで黙って混ざる（東京23区は名前が唯一なので安全だっただけ）。
    #    福岡交通圏の時刻表が福岡市だけで作ってあるのと同じ割り切り。
    "hiroshima": {
        "feed_label": "広島（アストラムライン・広電・JR）",  # 棚の索引に出す名前
        "pref": "広島県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "広島市",
        "slug": "hiroshima",
        "overpass_area": "広島市",
        "overpass_admin_level": "7",
        "overpass_filters": [
            '["railway"="tram_stop"]',
            '["railway"="station"]["station"="light_rail"]',
        ],
        # 実測（本通）: アストラムライン／広島電鉄循環線内回り・外回り・１号線・
        #               ３号線・７号線
        # 実測（広電西広島）: 広島電鉄宮島線・２号線・３号線
        # 実測（広島）: ＪＲ山陽本線・可部線・呉線・芸備線・山陽新幹線
        "line_patterns": {
            "newtransit": [r"アストラムライン"],
            "tram": [r"広島電鉄"],
            "jr": [r"ＪＲ"],
            "private": [r"スカイレール"],
        },
        "terminals": [
            # ⚠️ 向洋・海田市・矢野は安芸郡（広島市外）なので入れない。
            #    宮島口・廿日市も廿日市市＝範囲外。
            "広島", "横川", "西広島", "広電西広島", "新白島", "天神川", "五日市",
            "大町", "広島港", "安芸長束", "下祗園", "古市橋", "白島",
        ],
        "search_suffix": "広島",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        # 広島市だけで 路面電車68＋新交通22＝90。周辺5市町ぶんは --count で確定する。
        "expected_stations": 90,
    },
    "okayama": {
        "feed_label": "岡山（岡電・JR）",  # 棚の索引に出す名前
        "pref": "岡山県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "岡山市",
        "slug": "okayama",
        "overpass_area": "岡山市",
        "overpass_admin_level": "7",
        "overpass_filters": ['["railway"="tram_stop"]'],
        # 実測（岡山駅前）: 岡山電気軌道東山線
        # 実測（岡山）: ＪＲ山陽本線・伯備線・津山線・赤穂線・桃太郎線・
        #               瀬戸大橋線・山陽新幹線
        "line_patterns": {
            "tram": [r"岡山電気軌道"],
            "jr": [r"ＪＲ"],
            "private": [r"水島臨海鉄道"],
        },
        "terminals": [
            "岡山", "東岡山", "西川原", "大元", "北長瀬", "庭瀬", "高島",
            "備前西市", "妹尾", "法界院", "備前一宮", "玉柏", "上道",
        ],
        "search_suffix": "岡山",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 16,  # 実測16（岡山電気軌道の停留場）
    },
    # 🔴 新潟には地下鉄・モノレール・新交通が無い。路面電車タグに1件ヒットするが
    #    中身は「新潟交通(株)新潟東部営業所」＝**バス営業所の誤タグ**なので拾わない。
    #    既定の地下鉄フィルタのまま（＝0駅）にして、ターミナル駅だけで作る。
    "niigata": {
        "feed_label": "新潟（JR）",  # 棚の索引に出す名前
        "pref": "新潟県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "新潟市",
        "slug": "niigata",
        "overpass_area": "新潟市",
        "overpass_admin_level": "7",
        # 実測（新潟）: ＪＲ信越本線・越後線・白新線・上越新幹線
        "line_patterns": {
            "jr": [r"ＪＲ"],
        },
        "terminals": [
            "新潟", "白山", "関屋", "青山", "小針", "寺尾", "内野", "越後石山",
            "亀田", "荻川", "新津", "新崎", "豊栄", "大形", "早通",
            "新潟大学前", "内野西が丘", "越後赤塚",
        ],
        "search_suffix": "新潟",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 0,  # 主役の乗り物が無い＝ターミナル駅だけで作る
    },
    "saitama": {
        "feed_label": "さいたま（ニューシャトル・JR・私鉄）",  # 棚の索引に出す名前
        "pref": "埼玉県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "さいたま市",
        "slug": "saitama",
        "overpass_area": "さいたま市",
        "overpass_admin_level": "7",
        "overpass_filters": [
            '["railway"="station"]["station"="subway"]',       # 埼玉高速鉄道（浦和美園）
            '["railway"="station"]["station"="light_rail"]',   # ニューシャトル
        ],
        # 実測（大宮）: ニューシャトル・東武アーバンパークライン・ＪＲ各線
        # 実測（浦和美園）: 埼玉高速鉄道／（所沢）: 西武新宿線・西武池袋線
        "line_patterns": {
            "newtransit": [r"ニューシャトル"],
            "subway": [r"埼玉高速鉄道"],
            "jr": [r"ＪＲ"],
            "private": [r"東武", r"西武"],
        },
        "terminals": [
            "大宮", "浦和", "北浦和", "南浦和", "武蔵浦和", "さいたま新都心",
            "与野", "与野本町", "南与野", "北与野", "東大宮", "宮原", "日進",
            "岩槻", "浦和美園", "東浦和", "中浦和", "西大宮", "土呂", "大宮公園",
            "指扇", "七里", "大和田",
        ],
        "search_suffix": "さいたま",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 7,  # 実測7（ニューシャトル6＋埼玉高速の浦和美園1）
    },
    "chiba": {
        "feed_label": "千葉（モノレール・JR・私鉄）",  # 棚の索引に出す名前
        "pref": "千葉県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "千葉市",
        "slug": "chiba",
        "overpass_area": "千葉市",
        "overpass_admin_level": "7",
        "overpass_filters": [
            '["railway"="station"]["station"="monorail"]',
            '["railway"="station"]["monorail"="yes"]',
        ],
        # 実測（千葉）: 千葉都市モノレール１号線・２号線・ＪＲ各線
        # 実測（京成千葉）: 京成千葉線／（松戸）: 京成松戸線
        "line_patterns": {
            "monorail": [r"千葉都市モノレール"],
            "jr": [r"ＪＲ"],
            "private": [r"京成", r"東武", r"新京成", r"北総", r"東葉高速"],
        },
        "terminals": [
            "千葉", "千葉みなと", "西千葉", "稲毛", "蘇我", "本千葉", "都賀",
            "幕張", "幕張本郷", "海浜幕張", "検見川浜", "稲毛海岸", "誉田",
            "鎌取", "千葉中央", "京成千葉", "京成稲毛", "検見川", "浜野",
        ],
        "search_suffix": "千葉",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 17,  # 実測17（千葉都市モノレール）
    },
    # 🔴 静岡地区は**静岡市と浜松市の2つの政令市**が同じ営業圏に入っている
    #    （運賃ブロック上、浜松交通圏は静岡地区の中）。営業圏1つに時刻表1本
    #    という作りなので、両市の駅をこの1本にまとめる。
    #    どちらも地下鉄・モノレールが無く、静鉄・遠鉄はふつうの鉄道扱いなので
    #    OSMの種別では拾えない＝ターミナル駅として名前で並べる。
    "shizuoka": {
        "feed_label": "静岡・浜松（JR・静鉄・遠鉄）",  # 棚の索引に出す名前
        "pref": "静岡県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "静岡市・浜松市",
        "slug": "shizuoka",
        "overpass_wards": ["静岡市", "浜松市"],  # どちらも全国で唯一の名前＝安全
        "overpass_admin_level": "7",
        # 実測（新静岡）: 静岡鉄道静岡清水線／（新浜松）: 遠州鉄道
        # 実測（掛川）: 天竜浜名湖鉄道／（静岡・浜松）: ＪＲ東海道本線・東海道新幹線
        "line_patterns": {
            "jr": [r"ＪＲ"],
            "private": [r"静岡鉄道", r"遠州鉄道", r"天竜浜名湖鉄道", r"大井川鐵道"],
        },
        "terminals": [
            # 静岡市（ＪＲ＋静岡鉄道静岡清水線）
            "静岡", "東静岡", "草薙", "安倍川", "用宗", "清水", "興津", "由比",
            "新静岡", "日吉町", "音羽町", "春日町", "柚木", "長沼", "古庄",
            "県総合運動場", "県立美術館前", "御門台", "狐ヶ崎", "桜橋", "入江岡",
            "新清水",
            # 浜松市（ＪＲ＋遠州鉄道）
            "浜松", "天竜川", "高塚", "舞阪", "弁天島", "新浜松", "第一通り",
            "遠州病院", "八幡", "助信", "曳馬", "上島", "自動車学校前",
            "さぎの宮", "積志", "遠州西ケ崎", "遠州小松", "浜北",
            "美薗中央公園", "遠州小林", "遠州芝本", "遠州岩水寺", "西鹿島",
        ],
        "search_suffix": "静岡",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 0,  # 主役の乗り物が無い＝ターミナル駅だけで作る
    },
    "kumamoto": {
        "feed_label": "熊本（市電・JR・熊本電鉄）",  # 棚の索引に出す名前
        "pref": "熊本県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "熊本市",
        "slug": "kumamoto",
        "overpass_area": "熊本市",
        "overpass_admin_level": "7",
        "overpass_filters": ['["railway"="tram_stop"]'],
        # 実測（熊本駅前・健軍町）: 熊本市電Ａ系統・Ｂ系統（**系統名は全角のＡ／Ｂ**）
        # 実測（上熊本）: 熊本電気鉄道菊池線／（藤崎宮前）: 熊本電気鉄道藤崎線
        # 実測（熊本）: ＪＲ鹿児島本線・豊肥本線・三角線・九州新幹線
        "line_patterns": {
            "tram": [r"熊本市電"],
            "jr": [r"ＪＲ"],
            "private": [r"熊本電気鉄道", r"南阿蘇鉄道"],
        },
        "terminals": [
            "熊本", "上熊本", "藤崎宮前", "水前寺", "新水前寺", "平成", "南熊本",
            "西熊本", "武蔵塚", "川尻", "富合", "竜田口", "東海学園前", "北熊本",
            "池田", "打越", "韓々坂", "崇城大学前", "西里",
        ],
        "search_suffix": "熊本",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 35,  # 実測35（熊本市電の停留場）
    },
    # 🔴 相模・鎌倉地区は「相模原市」1市ではなく、県央交通圏＋湘南交通圏の広域。
    #    政令市は相模原だけだが、藤沢・平塚・鎌倉・海老名・厚木も同じ営業圏なので
    #    ターミナル駅はその範囲で並べる。地下鉄・モノレール・路面電車は無い。
    #    ⚠️ 町村（寒川町・大磯町など）は**全国にありふれた名前**で別の県の同名
    #       自治体を掴む恐れがあるため、範囲は市だけにしてある。
    "sagami": {
        "feed_label": "相模・鎌倉（JR・小田急ほか）",  # 棚の索引に出す名前
        "pref": "神奈川県",  # 同名駅よけ（駅ページの住所がこの県で始まること）
        "city": "相模原市ほか",
        "slug": "sagami",
        "overpass_wards": [
            "相模原市", "藤沢市", "茅ヶ崎市", "平塚市", "鎌倉市", "海老名市",
            "厚木市", "大和市", "座間市", "綾瀬市", "秦野市", "伊勢原市", "逗子市",
        ],
        "overpass_admin_level": "7",
        # 実測（相模大野）: 小田急小田原線・江ノ島線／（橋本）: 京王相模原線・
        #   ＪＲ横浜線・ＪＲ相模線／（藤沢）: 江ノ島電鉄／（海老名・大和）: 相鉄本線
        # 🔴 この営業圏で唯一の「地下鉄」が **湘南台**（横浜市営地下鉄ブルーラインの
        #    終点・藤沢市）。--count で1駅出たのがこれ。パターンを入れないと
        #    せっかく拾った駅が1路線も一致せず落ちる。
        "line_patterns": {
            "subway": [r"横浜市営地下鉄"],
            "jr": [r"ＪＲ"],
            "private": [r"小田急", r"京王", r"相鉄", r"江ノ島電鉄", r"東急",
                        r"湘南モノレール"],
        },
        "terminals": [
            "相模大野", "小田急相模原", "橋本", "淵野辺", "古淵", "相模原", "上溝",
            "藤沢", "辻堂", "湘南台", "長後", "六会日大前", "善行", "茅ケ崎",
            "平塚", "大船", "鎌倉", "北鎌倉", "逗子", "海老名", "本厚木", "厚木",
            "愛甲石田", "伊勢原", "秦野", "大和", "座間", "相模大塚", "南林間",
            "中央林間",
        ],
        "search_suffix": "神奈川",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        # 主役の乗り物が無い＝ターミナル駅で作る。0にすると駅数ガードは働かない
        # （実測では湘南台1駅だけ拾えるが、1固定にすると些細なOSMの変化で
        #  取得そのものが止まってしまうので、ここは緩めて後段の8割ガードに任せる）。
        "expected_stations": 0,
    },
}


_OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# 取得数が目安からこれ以上外れたら「範囲指定を間違えている」とみなして
# 止める（指示書C §2.2：全駅に流す前に必ず駅数を確認すること）。
_COUNT_TOLERANCE = 0.3


def _overpass_query(q):
    d = None
    for attempt in range(3):
        for ep in _OVERPASS:
            try:
                req = urllib.request.Request(ep, data=q.encode("utf-8"),
                                             headers={"User-Agent": "route_transit_feed (city subway)"})
                d = json.load(urllib.request.urlopen(req, timeout=120))
                break
            except Exception as e:
                print(f"  [overpass] {ep} 失敗: {e}")
        if d:
            break
        time.sleep(5)
    if not d:
        raise RuntimeError("Overpass 全滅（時間をおいて再実行）")
    return d


# 既定の拾い方＝地下鉄の駅。北九州のようにモノレールが主役の都市は
# cfg["overpass_filters"] で差し替える。
_DEFAULT_FILTERS = [
    '["railway"="station"]["station"="subway"]',
    '["railway"="station"]["subway"="yes"]',
]


def _overpass_collect(cfg, filters):
    """cfg["overpass_area"]（単一市区町村）または cfg["overpass_wards"]
    （東京23区のように単一領域が無い都市向け・区名のOR検索）の範囲で、
    [filters] のいずれかに当たる駅ノードを取る。"""
    level = cfg.get("overpass_admin_level", "7")
    wards = cfg.get("overpass_wards")
    if wards:
        area_defs = "".join(
            f'area["name"="{w}"]["admin_level"="{level}"]->.a{i};'
            for i, w in enumerate(wards)
        )
        node_defs = "".join(
            f'node(area.a{i}){f};' for i in range(len(wards)) for f in filters
        )
        q = f'[out:json][timeout:90];{area_defs}({node_defs});out;'
    else:
        area = cfg["overpass_area"]
        node_defs = "".join(f'node(area.a){f};' for f in filters)
        q = ('[out:json][timeout:60];'
             f'area["name"="{area}"]["admin_level"="{level}"]->.a;'
             f'({node_defs});out;')
    d = _overpass_query(q)
    seen = {}
    for e in d.get("elements", []):
        nm = (e.get("tags") or {}).get("name")
        if nm and nm not in seen and e.get("lat"):
            seen[nm] = {"name": nm, "city": cfg["city"],
                        "lat": round(e["lat"], 5), "lng": round(e["lon"], 5)}
    return list(seen.values())


def overpass_stations(cfg):
    """その都市の主役の駅（既定＝地下鉄／北九州＝モノレール）。"""
    return _overpass_collect(cfg, cfg.get("overpass_filters", _DEFAULT_FILTERS))


def overpass_terminals(cfg):
    """cfg["terminals"] に挙げた主要ターミナル駅の座標を、同じ市域の
    「鉄道駅ぜんぶ」から名前で拾う（JR・私鉄はタグが路線ごとに違うため、
    種類で絞らず名前で当てる方が確実）。

    見つからなかった名前は戻り値の2つ目に返す。**黙って減らさない**
    （市外の駅を書いた／表記違い、をその場で気づけるようにするため）。"""
    names = cfg.get("terminals") or []
    if not names:
        return [], []
    # 市内の駅を全部引くと重く、Overpassが504を返しがち（京都で実測）。
    # 欲しい名前だけに絞って問い合わせる。**大小のカナのゆれ**（ケ/ヶ・ツ/ッ）は
    # 正規表現の側で両方許す＝OSMが「保土ヶ谷」、正式表記が「保土ケ谷」でも当たる。
    def pat(n):
        return "".join(
            "[ケヶ]" if c in "ケヶ" else
            "[ツッ]" if c in "ツッ" else re.escape(c)
            for c in n
        )
    # 🔴 路面電車の停留場は railway=station ではなく **railway=tram_stop**。
    #    駅だけを見ていると、堺の「浜寺駅前」（阪堺電気軌道）のような
    #    路面電車のターミナルが1つも当たらない（2026-08-03 に実測して判明）。
    name_re = f'["name"~"^({"|".join(pat(n) for n in names)})$"]'
    allst = _overpass_collect(cfg, [f'["railway"="station"]{name_re}',
                                    f'["railway"="tram_stop"]{name_re}'])

    # 照合はゆれを潰した形で行い、**出す名前は設定に書いた正式表記**にする
    # （Yahooの検索も画面表示もこちらの方が当たりが良い）。座標はOSMのものを使う。
    def norm(s):
        return s.replace("ヶ", "ケ").replace("ッ", "ツ")
    by_norm = {norm(s["name"]): s for s in allst}
    found, missing = [], []
    for n in names:
        hit = by_norm.get(norm(n))
        if hit:
            found.append({**hit, "name": n})
        else:
            missing.append(n)
    return found, missing


# GitHubの実行環境は世界標準時(UTC)で動くため、time.strftime に "+09:00" を
# 付けただけでは **9時間古い日本時刻** が記録される（2026-08-01 に判明）。
# 日本時刻を明示して作る。
def jst_now_iso():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%dT%H:%M:%S+09:00")


def _search_name(name):
    """Yahooで検索する時の駅名（副題〈…〉や（…）を落とした形）。"""
    return re.sub(r"[〈（(].*?[〉）)]", "", name).strip() or name


def check_station_count(slug):
    """Yahooには行かず、OSMの駅数だけを目安と突き合わせる（--count 専用）。"""
    cfg = CONFIGS[slug]
    stations = overpass_stations(cfg)
    expected = cfg["expected_stations"]
    ratio = len(stations) / expected if expected else 1
    ok = (1 - _COUNT_TOLERANCE) <= ratio <= (1 + _COUNT_TOLERANCE)
    mark = "OK" if ok else "NG"
    print(f'[{mark}] {cfg["city"]}: OSM取得 {len(stations)}駅 / 目安 {expected}駅')
    if not ok:
        print("  → 範囲指定(overpass_area / overpass_wards / admin_level)を疑うこと。"
              "全駅スクレイプは実行しないこと。")
    return ok, stations


def main():
    args = sys.argv[1:]
    if not args or args[0] not in CONFIGS:
        print(f"使い方: python3 build_city_subway.py <{'|'.join(CONFIGS)}> [--count]")
        sys.exit(1)
    slug = args[0]
    count_only = "--count" in args
    cfg = CONFIGS[slug]

    if count_only:
        check_station_count(slug)
        return

    ok, stations = check_station_count(slug)
    if not ok:
        print("[GUARD] 駅数が目安から大きく外れています。--count で範囲指定を見直してから再実行してください。",
              file=sys.stderr)
        sys.exit(1)

    # 主要ターミナル駅（JR・私鉄）を足す（社長指示 2026-08-01）。
    # 地下鉄駅と同名のものは足さない（重複させない）。
    if cfg.get("terminals"):
        term, missing = overpass_terminals(cfg)
        have = {s["name"] for s in stations}
        added = [t for t in term if t["name"] not in have]
        stations = stations + added
        print(f'  ターミナル駅: 追加 {len(added)}駅 / 指定 {len(cfg["terminals"])}駅'
              + (f' / OSMに見つからず {missing}' if missing else ""))

    for s in stations:
        # どの駅にも都市の全路線キーを渡す。地下鉄駅にJRが乗り入れていれば
        # そのJRも一緒に取れる（＝ターミナルの狙いがそのまま満たせる）。
        # 乗り入れが無ければ一致しないだけで、余計な取得は起きない。
        s["lines"] = list(cfg["line_patterns"].keys())
        # 検索語からは駅名の副題を外す。OSMは「押上〈スカイツリー前〉」
        # 「明治神宮前〈原宿〉」のように〈〉付きで持っているが、Yahooの検索では
        # そのままだと1件も当たらない（2026-08-01 東京で3駅取りこぼした）。
        # 出力する駅名は〈〉付きのまま＝画面表記は変えない。
        s["search"] = f'{_search_name(s["name"])} {cfg["search_suffix"]}'
    os.makedirs("targets", exist_ok=True)
    json.dump({"city": cfg["city"], "stations": stations},
              open(f'targets/{cfg["slug"]}_subway.json', "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f'{cfg["city"]} 対象 {len(stations)}駅（OSM・地下鉄/モノレール＋ターミナル）')

    bt.LINE_PATTERNS = cfg["line_patterns"]          # 都市の路線パターンに差し替え
    bt.REQUIRE_PREF = cfg.get("pref", "")            # 同名の別県の駅を掴まないための壁
    result, failed = bt.scrape_all(stations)
    print(f"成功 {len(result)}駅 / 失敗 {len(failed)}件")

    # 取得できた駅が「実際に狙った駅数」の8割を切ったら配信しない（取得崩れの検知）。
    #
    # ⚠️ 目安(expected_stations)ではなく **狙った駅数(len(stations))** と比べる。
    #    OSMの範囲には地下鉄以外の駅（東京ならりんかい線・京急・東急）も混ざり、
    #    それらは路線パターンに一致しないので必ず失敗する。東京は227駅中20駅が
    #    これに当たり、目安280の8割(224駅)には**正常に取れても構造的に届かない**
    #    ＝ 2026-08-01 に東京の取得がここで止まった（実データは正常だった）。
    floor = int(len(stations) * 0.8)
    if len(result) < floor:
        print(f"[GUARD] 成功 {len(result)}駅 < 対象{len(stations)}駅の8割({floor}) "
              "→ 出力しない(異常)", file=sys.stderr)
        if failed:
            print("失敗:", failed[:20])
        sys.exit(1)

    data = {"version": 1, "generated_at": jst_now_iso(),
            "source": "https://transit.yahoo.co.jp/timetable/",
            "attribution": cfg["attribution"], "city": cfg["city"], "stations": result}
    os.makedirs("out", exist_ok=True)
    outp = f'out/{cfg["slug"]}_subway_timetable.json'
    json.dump(data, open(outp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f'📝 {outp} ({os.path.getsize(outp)/1024/1024:.2f} MB)')
    if failed:
        print("失敗（末尾20件）:", failed[:20])


if __name__ == "__main__":
    main()
