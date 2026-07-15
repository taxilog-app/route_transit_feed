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

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
BASE = "https://transit.yahoo.co.jp"
THROTTLE = float(os.environ.get("THROTTLE", "2.0"))  # 礼儀: 2秒以上
FEED_URL = "https://kirinkatanaboy-spec.github.io/route_transit_feed/train_timetable.json"
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


def find_station_id(name: str):
    url = f"{BASE}/timetable/search?q={urllib.parse.quote(name)}"
    html, final_url = fetch(url)
    m = re.match(r'.*/timetable/(\d+)(?:[/?].*)?$', final_url)
    if m:
        return m.group(1)
    matches = re.findall(
        r'href="/timetable/(\d+)(?:\?[^"]*)?"[^>]*>\s*([^<]+?)\s*</a>', html)
    norm = re.sub(r'[（(].*?[）)]', '', name).strip()
    seen, cands = set(), []
    for sid, label in matches:
        if sid in seen:
            continue
        seen.add(sid)
        cands.append((sid, label.strip()))
    for sid, label in cands:
        if label == name or label == norm:
            return sid
    for sid, label in cands:
        if norm in label or label in norm:
            return sid
    return cands[0][0] if cands else None


def get_lines_at_station(station_id: str) -> list[dict]:
    html, _ = fetch(f"{BASE}/timetable/{station_id}")
    data = extract_next_data(html)
    di = data["props"]["pageProps"].get("directionDetail", {}).get("directionItem", {})
    out = []
    for r in di.get("routeInfos", []):
        rail_name = r.get("railName", "")
        for g in r.get("railGroup", []):
            out.append({"rail_name": rail_name, "direction": g.get("direction", ""),
                        "rail_id": g.get("groupId", "")})
    return out


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
        try:
            sid = find_station_id(name)
            time.sleep(THROTTLE)
        except Exception as e:
            failed.append((name, "search", str(e)))
            continue
        if not sid:
            failed.append((name, "noid", ""))
            continue
        try:
            lines = get_lines_at_station(sid)
            time.sleep(THROTTLE)
        except Exception as e:
            failed.append((name, "lines", str(e)))
            continue
        # 対象路線のみ・rail_id重複除去
        matched, seen_rail = [], set()
        for line in lines:
            for key in target_keys:
                if any(re.search(p, line["rail_name"]) for p in LINE_PATTERNS.get(key, [])):
                    if line["rail_id"] not in seen_rail:
                        seen_rail.add(line["rail_id"])
                        matched.append({**line, "line_key": key})
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

    gen = prev_generated_at(stations) or time.strftime("%Y-%m-%dT%H:%M:%S+09:00")
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
