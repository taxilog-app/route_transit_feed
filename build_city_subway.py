#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""都市地下鉄の終電/時刻表ビルダー（福岡以外・ODPT非対応都市用）。

- 駅リスト＋座標は OSM(Overpass) から自動取得（¥0）
- 時刻表は build_train.py の関数を再利用して Yahoo!路線情報 から取得（¥0・全国対応）
- CONFIG を差し替えれば大阪・名古屋などにも同じ手順で展開できる

出力: out/<slug>_subway_timetable.json（train_timetable.json と同じスキーマ）
"""
import json, os, time, urllib.request

os.chdir(os.path.dirname(os.path.abspath(__file__)))
import build_train as bt  # fetch/find_station_id/get_lines_at_station/get_timetable/scrape_all を再利用

# ── 都市設定（ここを差し替えれば他都市へ）──
CONFIG = {
    "city": "札幌市",
    "slug": "sapporo",
    "overpass_area": "札幌市",
    "line_patterns": {"subway": [r"札幌市営地下鉄"]},  # Yahoo表記に一致・JR等を除外
    "search_suffix": "札幌",                          # 同名駅対策（出力名はcleanなまま）
    "attribution": "出典: Yahoo!路線情報（駅時刻表）／駅位置: OpenStreetMap contributors",
}


_OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def overpass_stations(area):
    q = ('[out:json][timeout:60];'
         f'area["name"="{area}"]["admin_level"="7"]->.a;'
         '(node(area.a)["railway"="station"]["station"="subway"];'
         ' node(area.a)["railway"="station"]["subway"="yes"];);out;')
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
    seen = {}
    for e in d.get("elements", []):
        nm = (e.get("tags") or {}).get("name")
        if nm and nm not in seen and e.get("lat"):
            seen[nm] = {"name": nm, "city": CONFIG["city"],
                        "lat": round(e["lat"], 5), "lng": round(e["lon"], 5)}
    return list(seen.values())


def main():
    cfg = CONFIG
    stations = overpass_stations(cfg["overpass_area"])
    for s in stations:
        s["lines"] = list(cfg["line_patterns"].keys())
        s["search"] = f'{s["name"]} {cfg["search_suffix"]}'
    os.makedirs("targets", exist_ok=True)
    json.dump({"city": cfg["city"], "stations": stations},
              open(f'targets/{cfg["slug"]}_subway.json', "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f'{cfg["city"]} 地下鉄 対象 {len(stations)}駅（OSM）')

    bt.LINE_PATTERNS = cfg["line_patterns"]          # 都市の路線パターンに差し替え
    result, failed = bt.scrape_all(stations)
    print(f"成功 {len(result)}駅 / 失敗 {len(failed)}件")

    data = {"version": 1, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
            "source": "https://transit.yahoo.co.jp/timetable/",
            "attribution": cfg["attribution"], "city": cfg["city"], "stations": result}
    os.makedirs("out", exist_ok=True)
    outp = f'out/{cfg["slug"]}_subway_timetable.json'
    json.dump(data, open(outp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f'📝 {outp} ({os.path.getsize(outp)/1024/1024:.2f} MB)')
    if failed:
        print("失敗:", failed[:10])


if __name__ == "__main__":
    main()
