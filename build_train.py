#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_transit_feed — JR/西鉄 駅時刻表フィード（Phase3・JR/西鉄）。

stations_target.json の全駅 × 対象路線 × 平日/土曜/日祝 の時刻表を
Yahoo!路線情報（__NEXT_DATA__）から取得し、アプリ TrainService の既存スキーマと
一致する train_timetable.json（minify）を out/ に生成する。

- 源=Yahoo（グレー）。月1のみ・2秒スロットルで露出を最小化。
- 差分検知: 既存の公開版と駅データが不変なら generated_at を据置（更新日を正直に保つ）。
- 駅数下限ガードで取得崩れ時は配信中止（Pagesは前回分を温存）。
スクレイプ処理はアプリの tools/scrape_all_stations.py（実績あり）を移植。
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# ブラウザ偽装をやめ、正直な名乗り（他feedと統一）。Yahooは正直UAでも __NEXT_DATA__ を返す。
UA = "route_transit_feed/0.1 (+https://github.com/taxilog-app/route_transit_feed)"
BASE = "https://transit.yahoo.co.jp"
THROTTLE = float(os.environ.get("THROTTLE", "2.0"))  # 礼儀: 2秒以上
FEED_URL = "https://taxilog-app.github.io/route_transit_feed/train_timetable.json"
MIN_STATIONS = 40  # これ未満は取得崩れとみなし配信中止

# 路線key → Yahoo路線名パターン
LINE_PATTERNS = {
    "kagoshima": [r"鹿児島本線"], "kashii": [r"香椎線"],
    "sasaguri": [r"福北ゆたか線", r"篠栗線"], "chikuhi": [r"筑肥線"],
    "shinkansen_sanyo": [r"山陽新幹線"], "shinkansen_kyushu": [r"九州新幹線"],
    "nishitetsu_omuta": [r"天神大牟田線", r"大牟田線"], "nishitetsu_dazaifu": [r"太宰府線"],
}


def fetch(url: str):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace"), r.url


def extract_next_data(html: str) -> dict:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
        html, re.DOTALL)
    if not m:
        raise RuntimeError("__NEXT_DATA__ not found")
    return json.loads(m.group(1))


def find_station_ids(name: str, query=None):
    """優先順位付きの候補駅IDリストを返す（直接リダイレクトなら1件のみ）。

    同名の乗換駅（例:「梅田」で 大阪梅田(阪急線)/梅田(地下鉄)/大阪梅田(阪神線) が
    並ぶ、「なんば」で なんば(南海線)/なんば(地下鉄) が並ぶ 等）は、単純な
    先頭一致やsubstring一致だけでは違う事業者の駅を拾ってしまう（実測・大阪
    メトロのスクレイプで判明）。呼び出し側（scrape_all）が対象路線と一致する
    候補が見つかるまで順に試せるよう、1件に絞らずリストで返す。
    """
    # 検索は query(例 "春日 福岡")で同名駅を避ける。照合・出力は clean な name のまま。
    url = f"{BASE}/timetable/search?q={urllib.parse.quote(query or name)}"
    html, final_url = fetch(url)
    m = re.match(r'.*/timetable/(\d+)(?:[/?].*)?$', final_url)
    if m:
        return [m.group(1)]
    matches = re.findall(
        r'href="/timetable/(\d+)(?:\?[^"]*)?"[^>]*>\s*([^<]+?)\s*</a>', html)
    # 副題は〈〉付きでも来る（押上〈スカイツリー前〉/明治神宮前〈原宿〉）。
    # Yahooの表示は副題なしなので、照合用に落としておく。
    norm = re.sub(r'[〈（(].*?[〉）)]', '', name).strip()
    seen, cands = set(), []
    for sid, label in matches:
        if sid in seen:
            continue
        seen.add(sid)
        cands.append((sid, label.strip()))

    exact = [sid for sid, label in cands if label == name or label == norm]
    # 「駅名(◯◯線)」形式の候補は、末尾の(...)注記を外した上で完全一致するかを
    # 次点で見る（優先度2）。
    core_exact = [sid for sid, label in cands
                  if re.sub(r'[（(][^）)]*[）)]\s*$', '', label).strip() == norm]
    fuzzy = [sid for sid, label in cands if norm in label or label in norm]
    all_ids = [sid for sid, _ in cands]

    ordered, seen2 = [], set()
    for group in (exact, core_exact, fuzzy, all_ids):
        for sid in group:
            if sid not in seen2:
                seen2.add(sid)
                ordered.append(sid)
    return ordered


def find_station_id(name: str, query=None):
    ids = find_station_ids(name, query=query)
    return ids[0] if ids else None


def get_station_page(station_id: str) -> tuple[list[dict], str]:
    """駅ページ1枚から「乗り入れ路線」と「住所」をまとめて取る。

    住所は同名駅の取り違えを弾くために使う（REQUIRE_PREF）。同じページから
    取るので取得回数は増えない。"""
    html, _ = fetch(f"{BASE}/timetable/{station_id}")
    data = extract_next_data(html)
    pp = data["props"]["pageProps"]
    di = pp.get("directionDetail", {}).get("directionItem", {})
    out = []
    for r in di.get("routeInfos", []):
        rail_name = r.get("railName", "")
        for g in r.get("railGroup", []):
            out.append({"rail_name": rail_name, "direction": g.get("direction", ""),
                        "rail_id": g.get("groupId", "")})
    addr = (pp.get("lipFeature") or {}).get("Address", "") or ""
    return out, addr


def get_lines_at_station(station_id: str) -> list[dict]:
    return get_station_page(station_id)[0]


# 🔴 同名駅よけ（2026-08-01）。都市ビルダーが「この都道府県の駅であること」を
#    入れると、駅ページの住所が違う県だった候補を捨てる。
#
#    実害：京都の「桂川」を取りに行って **福岡県嘉穂郡桂川町の桂川駅** を掴んだ
#    （ＪＲ原田線・福北ゆたか線が京都の時刻表に混ざった）。地下鉄だけを見ていた
#    頃は路線名（京都市営地下鉄…）が壁になっていたが、ターミナル駅のために
#    「ＪＲ」という広い網を入れた途端、全国の同名駅が通ってしまう。
REQUIRE_PREF = ""


def get_timetable(station_id: str, rail_id: str, kind: int) -> dict:
    html, _ = fetch(f"{BASE}/timetable/{station_id}/{rail_id}?kind={kind}")
    data = extract_next_data(html)
    tt = data["props"]["pageProps"].get("timetableItem", {})
    master = tt.get("master", {})

    def build_map(raw):
        out = {}
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and str(item.get("id", "")):
                    out[str(item.get("id", ""))] = item.get("name", "")
        return out
    kind_map = build_map(master.get("kind") if isinstance(master, dict) else None)
    dest_map = build_map(master.get("destination") if isinstance(master, dict) else None)

    departures = []
    for hb in tt.get("hourTimeTable", []) or []:
        hour = int(hb.get("hour", "0"))
        for mt in hb.get("minTimeTable", []) or []:
            departures.append({
                "h": hour, "m": int(mt.get("minute", "0")),
                "k": kind_map.get(str(mt.get("kindId", "")), ""),
                "d": dest_map.get(str(mt.get("destinationId", "")), ""),
                "tn": mt.get("trainName", ""),
                "fs": mt.get("firstStation") == "true",
            })
    return {"departures": departures}


def scrape_all(stations) -> tuple[dict, list]:
    result, failed = {}, []
    n = len(stations)
    for idx, st in enumerate(stations, 1):
        name = st["name"]
        target_keys = set(st["lines"])
        # まず駅名単体で検索する（Yahooはユニークな駅名なら直接リダイレクトで
        # 解決する）。"<駅名> <都市名>" の組み合わせクエリはYahoo側で
        # 「該当する駅はありませんでした」と0件になることがある(実測・大阪で
        # 89駅全滅の原因だった)ため、st["search"]（同名駅対策）は
        # 単体名で見つからなかった時のフォールバックとしてのみ使う。
        try:
            candidate_ids = find_station_ids(name, query=name)
            time.sleep(THROTTLE)
        except Exception as e:
            failed.append((name, "search", str(e)))
            continue
        if not candidate_ids:
            search_q = st.get("search")
            if search_q and search_q != name:
                try:
                    candidate_ids = find_station_ids(name, query=search_q)
                    time.sleep(THROTTLE)
                except Exception as e:
                    failed.append((name, "search_fallback", str(e)))
                    continue
        if not candidate_ids:
            failed.append((name, "noid", ""))
            continue

        # 同名の乗換駅（梅田/なんば等）は候補が複数返る。上位3件まで、対象路線
        # (target_keys)に一致するものが見つかるまで順に試す（無駄打ち防止の
        # 上限。ほとんどの駅は候補1件で即決するため実害は無い）。
        sid = None
        matched, seen_rail = [], set()
        for cid in candidate_ids[:3]:
            try:
                lines, addr = get_station_page(cid)
                time.sleep(THROTTLE)
            except Exception as e:
                failed.append((name, f"lines_{cid}", str(e)))
                continue
            # 住所が別の都道府県なら、同名の別の駅＝この候補は捨てる。
            # 住所が読めない時は捨てない（読めないこと自体は異常ではない）。
            if REQUIRE_PREF and addr and not addr.startswith(REQUIRE_PREF):
                failed.append((name, f"wrong_pref_{cid}", addr[:20]))
                continue
            cand_matched, cand_seen_rail = [], set()
            for line in lines:
                for key in target_keys:
                    if any(re.search(p, line["rail_name"]) for p in LINE_PATTERNS.get(key, [])):
                        if line["rail_id"] not in cand_seen_rail:
                            cand_seen_rail.add(line["rail_id"])
                            cand_matched.append({**line, "line_key": key})
                        break
            if cand_matched:
                sid, matched, seen_rail = cid, cand_matched, cand_seen_rail
                break
        if not matched:
            failed.append((name, "noroute", ""))
            continue
        sd = {"station_id": sid, "city": st["city"], "lat": st["lat"],
              "lng": st["lng"], "schedules": []}
        for ml in matched:
            for kind in (1, 2, 4):
                try:
                    tt = get_timetable(sid, ml["rail_id"], kind)
                    time.sleep(THROTTLE)
                except Exception as e:
                    failed.append((name, f"tt_{ml['rail_id']}_{kind}", str(e)))
                    continue
                sd["schedules"].append({
                    "line_key": ml["line_key"], "line_name": ml["rail_name"],
                    "direction": ml["direction"], "rail_id": ml["rail_id"],
                    "day_type": {1: "weekday", 2: "saturday", 4: "holiday"}[kind],
                    "departures": tt["departures"],
                })
        result[name] = sd
        print(f"  [{idx}/{n}] {name}: {len(sd['schedules'])}スケジュール")
    return result, failed


def prev_generated_at(stations: dict):
    """既存公開版を取得。駅データが今回と一致すれば前回のgenerated_atを返す（差分検知）。"""
    try:
        req = urllib.request.Request(FEED_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            prev = json.loads(r.read().decode("utf-8"))
        if prev.get("stations") == stations:
            return prev.get("generated_at")
    except Exception:
        pass
    return None


def main():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "stations_target.json"), encoding="utf-8") as f:
        stations_target = json.load(f)["stations"]
    print(f"対象 {len(stations_target)}駅を取得…")
    stations, failed = scrape_all(stations_target)
    print(f"成功 {len(stations)}駅 / 失敗 {len(failed)}件")

    if len(stations) < MIN_STATIONS:
        print(f"[GUARD] {len(stations)}駅 < 下限{MIN_STATIONS} → 配信中止(異常)",
              file=sys.stderr)
        sys.exit(1)

    gen = prev_generated_at(stations) or jst_now_iso()
    data = {
        "version": 1,
        "generated_at": gen,
        "source": "https://transit.yahoo.co.jp/timetable/",
        "attribution": "出典: Yahoo!路線情報（駅時刻表）",
        "stations": stations,
    }
    os.makedirs("out", exist_ok=True)
    with open("out/train_timetable.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    mb = os.path.getsize("out/train_timetable.json") / 1024 / 1024
    print(f"📝 out/train_timetable.json ({mb:.2f} MB, generated_at={gen})")


if __name__ == "__main__":
    main()
