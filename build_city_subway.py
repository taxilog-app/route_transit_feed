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
        "city": "大阪市",
        "slug": "osaka",
        "overpass_area": "大阪市",
        "overpass_admin_level": "7",
        # Yahoo実表記は「OsakaMetro御堂筋線」のようにスペース無し英字＋路線名
        # （実測確認済み。"大阪メトロ"/"Osaka Metro"では一致しない）。
        "line_patterns": {"subway": [r"OsakaMetro", r"大阪メトロ", r"Osaka\s*Metro"]},
        "search_suffix": "大阪",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
        "expected_stations": 100,
    },
    "nagoya": {
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


def overpass_stations(cfg):
    """cfg["overpass_area"]（単一市区町村）または cfg["overpass_wards"]
    （東京23区のように単一領域が無い都市向け・区名のOR検索）から駅を取る。"""
    level = cfg.get("overpass_admin_level", "7")
    wards = cfg.get("overpass_wards")
    if wards:
        area_defs = "".join(
            f'area["name"="{w}"]["admin_level"="{level}"]->.a{i};'
            for i, w in enumerate(wards)
        )
        node_defs = "".join(
            f'node(area.a{i})["railway"="station"]["station"="subway"];'
            f'node(area.a{i})["railway"="station"]["subway"="yes"];'
            for i in range(len(wards))
        )
        q = f'[out:json][timeout:90];{area_defs}({node_defs});out;'
    else:
        area = cfg["overpass_area"]
        q = ('[out:json][timeout:60];'
             f'area["name"="{area}"]["admin_level"="{level}"]->.a;'
             '(node(area.a)["railway"="station"]["station"="subway"];'
             ' node(area.a)["railway"="station"]["subway"="yes"];);out;')
    d = _overpass_query(q)
    seen = {}
    for e in d.get("elements", []):
        nm = (e.get("tags") or {}).get("name")
        if nm and nm not in seen and e.get("lat"):
            seen[nm] = {"name": nm, "city": cfg["city"],
                        "lat": round(e["lat"], 5), "lng": round(e["lon"], 5)}
    return list(seen.values())


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

    for s in stations:
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
    print(f'{cfg["city"]} 地下鉄 対象 {len(stations)}駅（OSM）')

    bt.LINE_PATTERNS = cfg["line_patterns"]          # 都市の路線パターンに差し替え
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
