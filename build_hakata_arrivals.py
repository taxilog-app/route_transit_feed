"""博多駅に「着く」新幹線の一覧（2026-10-28・AI日報／本日のAI戦略の手がかり）。

博多の時刻表は「博多を出る」便しか無い。着く便は手前の駅の「博多方面」の時刻表から
割り出す（Yahoo!路線情報・build_train.py と同じ取り方・2秒スロットル）。
  - 小倉   山陽新幹線 博多方面 → +16分（こだまは+19分）＝東京・大阪から
  - 新鳥栖 九州新幹線 博多方面 → +13分（つばめは+14分）＝鹿児島・熊本から（みずほは通過）
  - 熊本   九州新幹線 博多方面 → みずほだけ +33分
所要は時刻表の代表値（数分ずれることがある＝目安）。駅の時刻表の一覧（train_timetable.json）
には混ぜない＝アプリの駅選びに小倉・熊本が出ない。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import build_train as bt

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out",
                   "hakata_shinkansen_arrivals.json")

# (駅名, 検索語, 路線の正規表現, 出す種別の条件, 所要(分)を決める関数, どこからの便か)
SOURCES = [
    ("小倉", "小倉 北九州", r"山陽新幹線", lambda k: True,
     lambda k: 19 if "こだま" in k else 16, "東京・大阪方面から"),
    ("新鳥栖", "新鳥栖", r"九州新幹線", lambda k: "みずほ" not in k,
     lambda k: 14 if "つばめ" in k else 13, "鹿児島・熊本方面から"),
    ("熊本", "熊本 熊本", r"九州新幹線", lambda k: "みずほ" in k,
     lambda k: 33, "鹿児島・熊本方面から"),
]
DAY_TYPES = {1: "weekday", 2: "saturday", 4: "holiday"}


def main() -> int:
    bt.REQUIRE_PREF = ""  # 福岡県の外の駅（新鳥栖＝佐賀・熊本）も取る
    out: dict[str, list] = {v: [] for v in DAY_TYPES.values()}
    for name, q, rail_re, want, minutes, frm in SOURCES:
        sid = bt.find_station_id(name, query=q)
        time.sleep(bt.THROTTLE)
        if not sid:
            print(f"[NG] {name}: 駅が見つからない", file=sys.stderr)
            return 1
        lines, addr = bt.get_station_page(sid)
        time.sleep(bt.THROTTLE)
        targets = [l for l in lines
                   if re.search(rail_re, l["rail_name"]) and "博多" in l["direction"]]
        if not targets:
            print(f"[NG] {name}: 博多方面の {rail_re} が無い（{addr}）", file=sys.stderr)
            return 1
        for t in targets:
            for kind, day in DAY_TYPES.items():
                tt = bt.get_timetable(sid, t["rail_id"], kind)
                time.sleep(bt.THROTTLE)
                for d in tt["departures"]:
                    k = d.get("k", "")
                    if not want(k):
                        continue
                    mins = (d["h"] * 60 + d["m"] + minutes(k)) % 1440
                    out[day].append({"h": mins // 60, "m": mins % 60, "k": k,
                                     "tn": d.get("tn", ""), "from": frm, "via": name})
    for day, lst in out.items():
        lst.sort(key=lambda x: (x["h"] * 60 + x["m"]))
        # 同じ列車を2つの駅から拾った時は1つに（列車名で）
        seen, uniq = set(), []
        for x in lst:
            key = (x["tn"], x["h"], x["m"] // 5)
            if x["tn"] and key in seen:
                continue
            seen.add(key)
            uniq.append(x)
        out[day] = uniq
        if len(uniq) < 50:  # 1日に博多へ着く新幹線は100本を大きく超える。少なすぎ＝取得崩れ
            print(f"[GUARD] {day}: {len(uniq)}本しか無い → 配信しない", file=sys.stderr)
            return 1
    data = {
        "version": 1,
        "generated_at": bt.jst_now_iso(),
        "source": "Yahoo!路線情報（小倉・新鳥栖・熊本の博多方面の時刻表から割り出し）",
        "attribution": "Yahoo!路線情報",
        "note": "博多着の時刻は手前の駅の出発＋代表の所要時間の目安（数分ずれることがある）",
        "day_types": out,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("博多着 " + " / ".join(f"{d}:{len(v)}本" for d, v in out.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
