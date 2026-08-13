#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""フェリー（福岡市営渡船）の到着時刻フィードを作る。

【何のためか】
港はタクシー需要の山。船が着く時刻に先回りできると、待たずに客を乗せられる。

【出どころ】福岡市オープンデータ（港湾空港局 総務部 客船事務所）
  データセット https://data.bodik.jp/dataset/401307_shieitosen2025
  ライセンス   CC BY 4.0（商用利用可・出典表示のみ）＝**照会不要**
  形式         GTFS（世界共通の時刻表形式）・zip 17KB

【🔴 この道具の要点】
  ① **到着だけ**を出す。運転手が知りたいのは「いつ客が港から出てくるか」。
     出発便は乗る客がタクシーで“来る”側なので、需要はもう発生し終わっている。
  ② **本土側の港だけ**を出す。志賀島・能古・玄界島・小呂島・西戸崎の待合所は
     島側で、そこに着く便にタクシー需要は無い（島にタクシーが居ない）。
  ③ `calendar_dates.txt` の例外日を**必ず読む**。祝日・年末年始でダイヤが
     変わるので、無視すると嘘の時刻を出す。

使い方:
    python3 build_ferry.py            # 出力 out/ferry_timetable.json
    python3 build_ferry.py --check    # 作らずに検査だけ
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out", "ferry_timetable.json")

DATASET_PAGE = "https://data.bodik.jp/dataset/401307_shieitosen2025"
GTFS_URL = ("https://data.bodik.jp/dataset/9938b52c-e54c-4d92-9975-a98c5f60e727"
            "/resource/499f5b3d-093e-4c32-9636-2b91b227e6c2/download/data.zip")
LICENSE = "CC BY 4.0"
ATTRIBUTION = "出典: 福岡市（港湾空港局 総務部 客船事務所）「福岡市営渡船 運行情報データ（GTFS形式）」"

# 🔴 本土側の港だけ。ここに無い港（島側）は出さない。
#    営業圏キーは**配信キー**（アプリの loadFeedAreaKey が返す物）と一致させること。
MAINLAND_PORTS = {
    "[01]博多ふ頭第一ターミナル": {"short": "博多ふ頭", "area": "fukuoka"},
    "[04]姪浜旅客待合所": {"short": "姪浜", "area": "fukuoka"},
}


def fetch(url, timeout=120):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def read_gtfs(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    def rd(name):
        if name not in z.namelist():
            return []
        return list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig"))))
    return {n: rd(n) for n in (
        "feed_info.txt", "routes.txt", "stops.txt", "trips.txt",
        "stop_times.txt", "calendar.txt", "calendar_dates.txt")}


def services_of(cal, cal_dates):
    """運航パターン（service_id）ごとの「どの日に走るか」の規則をそのまま出す。

    🔴 電車と同じ「平日／土／日祝」の3区分に当てはめてはいけない（2026-08-13 実測）。
       このデータは3区分になっていない:
         ・能古/玄界島 … 全日ダイヤ（曜日で分かれない）
         ・志賀島の「平日ダイヤ」… 実は**月〜土**（土曜を含む）
         ・小呂島 … 月水金／火木土日で交互
         ・さらに夏/冬ダイヤ・年末年始ダイヤが別にある
       3区分に押し込むと**別の曜日の時刻を出す**（実際に一度やらかした）。
       GTFS 本来の形どおり「日付で判定する」規則を配り、端末側で今日の便を出す。
    """
    DAYS = ("monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday")
    out = {}
    for c in cal:
        out[c["service_id"]] = {
            # [月,火,水,木,金,土,日] の0/1
            "days": [1 if c.get(d) == "1" else 0 for d in DAYS],
            "from": c.get("start_date", ""),
            "to": c.get("end_date", ""),
            "add": [],      # 例外的に「走る」日（祝日ダイヤ等）
            "remove": [],   # 例外的に「走らない」日（年末年始の欠航等）
        }
    for r in cal_dates:
        sid = r["service_id"]
        if sid not in out:
            out[sid] = {"days": [0] * 7, "from": "", "to": "",
                        "add": [], "remove": []}
        key = "add" if r.get("exception_type") == "1" else "remove"
        out[sid][key].append(r["date"])
    for v in out.values():
        v["add"].sort()
        v["remove"].sort()
    return out


def runs_on(svc, date):
    """その運航パターンが [date]（datetime.date）に走るか。端末側と同じ判定。"""
    ymd = date.strftime("%Y%m%d")
    if ymd in svc["remove"]:
        return False
    if ymd in svc["add"]:
        return True
    if svc["from"] and ymd < svc["from"]:
        return False
    if svc["to"] and ymd > svc["to"]:
        return False
    return svc["days"][date.weekday()] == 1


def build():
    print("① オープンデータを取得中 …", flush=True)
    g = read_gtfs(fetch(GTFS_URL))

    info = g["feed_info.txt"][0] if g["feed_info.txt"] else {}
    start, end = info.get("feed_start_date", ""), info.get("feed_end_date", "")
    print(f"   有効期間 {start}〜{end} / 版 {info.get('feed_version','?')}")

    routes = {r["route_id"]: (r.get("route_long_name") or r.get("route_short_name"))
              for r in g["routes.txt"]}
    stops = {s["stop_id"]: s for s in g["stops.txt"]}
    trips = {t["trip_id"]: t for t in g["trips.txt"]}
    services = services_of(g["calendar.txt"], g["calendar_dates.txt"])

    # ② 各便の停車順を組み立て、「終着＝到着」と「どこから来たか＝始発」を取る
    seq = {}
    for st in g["stop_times.txt"]:
        seq.setdefault(st["trip_id"], []).append(st)
    for v in seq.values():
        v.sort(key=lambda x: int(x["stop_sequence"]))

    ports, skipped_island, used = {}, 0, set()
    for tid, sts in seq.items():
        last, first = sts[-1], sts[0]
        pid = last["stop_id"]
        if pid not in MAINLAND_PORTS:      # 🔴 島側に着く便は出さない
            skipped_island += 1
            continue
        t = trips.get(tid, {})
        sid = t.get("service_id", "")
        if sid not in services:
            continue
        used.add(sid)
        hh, mm = last["arrival_time"].split(":")[:2]
        p = ports.setdefault(pid, {
            "name": stops[pid]["stop_name"],
            "short": MAINLAND_PORTS[pid]["short"],
            "area": MAINLAND_PORTS[pid]["area"],
            "lat": float(stops[pid]["stop_lat"]),
            "lng": float(stops[pid]["stop_lon"]),
            "arrivals": [],
        })
        p["arrivals"].append({
            "h": int(hh) % 24,          # GTFSは24時超えを許す（25:10 等）
            "m": int(mm),
            "from": stops[first["stop_id"]]["stop_name"],
            "route": routes.get(t.get("route_id"), ""),
            "svc": sid,                 # どの運航パターンか（端末が日付で判定する）
        })

    for p in ports.values():
        p["arrivals"].sort(key=lambda x: (x["h"], x["m"]))

    doc = {
        "version": 2,   # 🔴 v1は「平日/土/日祝」の3区分だった＝このデータに合わず廃止
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
                          .isoformat(timespec="seconds"),
        "source": DATASET_PAGE,
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "valid_from": start,
        "valid_to": end,
        # 使われている運航パターンだけ配る（未使用の規則を端末へ送らない）
        "services": {k: v for k, v in services.items() if k in used},
        "ports": list(ports.values()),
    }

    ng = check(doc, skipped_island)
    if ng:
        sys.exit(1)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"\n✓ 書き出しました: {OUT} ({os.path.getsize(OUT)/1024:.1f} KB)")
    return 0


def check(doc, skipped_island=0):
    """🔴 公開前の検査。1つでも赤なら出さない。

    配管総点検（2026-08-13）で踏んだ壊れ方を、最初から弾く形にしてある。
    """
    print("\n② 検査")
    ng = 0
    ports, services = doc["ports"], doc["services"]

    if not ports:
        print("   ❌ 港が0件。本土側の港の名前が変わった疑い"); ng += 1
    for p in ports:
        if not p["arrivals"]:
            print(f"   ❌ {p['short']}: 到着が0件"); ng += 1
        else:
            print(f"   ✅ {p['short']}（{p['area']}）: 到着 {len(p['arrivals'])}便")

    # 座標が福岡交通圏の中にあるか（別の街のデータを掴んでいないか）
    for p in ports:
        if not (33.4 <= p["lat"] <= 33.8 and 130.0 <= p["lng"] <= 130.6):
            print(f"   ❌ {p['short']}: 座標が福岡の外 {p['lat']},{p['lng']}"); ng += 1

    # 🔴 便が参照する運航パターンが、全部そろっているか
    miss = {a["svc"] for p in ports for a in p["arrivals"]} - set(services)
    if miss:
        print(f"   ❌ 運航パターンが足りない: {sorted(miss)}"); ng += 1
    else:
        print(f"   ✅ 運航パターン {len(services)}種すべて揃っている")

    # 🔴 実際の日付で数えて、どの日も0便にならないか（3区分の失敗の再発防止）
    today = dt.date.today()
    zero = []
    for i in range(14):
        d = today + dt.timedelta(days=i)
        act = {k for k, v in services.items() if runs_on(v, d)}
        for p in ports:
            n = sum(1 for a in p["arrivals"] if a["svc"] in act)
            if n == 0:
                zero.append(f"{d} {p['short']}")
    if zero:
        print(f"   ❌ 到着0便の日がある: {zero[:4]}"); ng += 1
    else:
        for p in ports:
            act = {k for k, v in services.items() if runs_on(v, today)}
            n = sum(1 for a in p["arrivals"] if a["svc"] in act)
            print(f"   ✅ {p['short']}: 本日({today}) {n}便")
        print("   ✅ 今日から2週間、どの日も到着が0便にならない")

    if doc["valid_to"] and doc["valid_to"] < today.strftime("%Y%m%d"):
        print(f"   ❌ 有効期間が切れている（{doc['valid_to']} まで）"); ng += 1
    else:
        print(f"   ✅ 有効期間 {doc['valid_from']}〜{doc['valid_to']}")

    if not doc.get("attribution") or not doc.get("source"):
        print("   ❌ 出典・ライセンスが入っていない（CC BY 4.0 の要件）"); ng += 1

    if skipped_island:
        print(f"   ℹ️ 島側に着く{skipped_island}便は意図的に除外（タクシー需要が無い）")
    print("   🔴 公開しないこと" if ng else "   🟢 公開して大丈夫です")
    return ng


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="既存の出力を検査するだけ（作り直さない）")
    a = ap.parse_args()
    if a.check:
        if not os.path.exists(OUT):
            sys.exit(f"{OUT} がまだありません")
        return 1 if check(json.load(open(OUT, encoding="utf-8"))) else 0
    return build()


if __name__ == "__main__":
    sys.exit(main())
