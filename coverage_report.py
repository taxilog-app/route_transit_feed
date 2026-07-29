#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_transit_feed / coverage_report.py

交通データ（地下鉄・在来線・新幹線・私鉄）の「取得率」を測る。
対象定義(stations_target.json) と 実出力(out/*.json) を突き合わせ、
路線別の 対象駅数 / 取得済駅数 / 取得率 / 状態 / 最終更新 を out/coverage.json に書き出す。

社長が「どの路線が完了/未取得か」を一覧で確認するためのもの。
データが増えたら再実行するだけで最新の取得状況になる。
"""
import json, os, datetime, glob

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")


def _load(p):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def _station_names(tt):
    """train_timetable.json から取得済み駅名の集合を best-effort で取り出す。"""
    if not tt:
        return set()
    s = tt.get("stations")
    if isinstance(s, dict):
        return set(s.keys())
    if isinstance(s, list):
        return {x.get("name") for x in s if isinstance(x, dict) and x.get("name")}
    return set()


def main():
    tgt = _load(os.path.join(HERE, "stations_target.json")) or {}
    lines = tgt.get("_lines", {})
    stations = tgt.get("stations", [])

    # ── 在来線・新幹線・私鉄 ──
    tt = _load(os.path.join(OUT, "train_timetable.json"))
    covered = _station_names(tt)
    tt_update = (tt or {}).get("generated_at") or (tt or {}).get("generatedAt")

    rail_lines = []
    for key, name in lines.items():
        tstations = [s for s in stations if key in s.get("lines", [])]
        cov = sum(1 for s in tstations if s["name"] in covered)
        n = len(tstations)
        rail_lines.append({
            "name": name, "target": n, "covered": cov,
            "rate": round(cov / n * 100) if n else 0,
            "status": "done" if n and cov >= n else ("partial" if cov else "todo"),
        })
    rail_target = len({s["name"] for s in stations})
    rail_cov = len({s["name"] for s in stations if s["name"] in covered})

    # ── 地下鉄（福岡市地下鉄） ──
    sub = _load(os.path.join(OUT, "subway_timetable.json"))
    sub_update = (sub or {}).get("generated_at")
    sub_lines = []
    if sub:
        by_line = {}
        for nm, st in sub.get("stations", {}).items():
            for sc in st.get("schedules", []):
                by_line.setdefault(sc.get("line", "?"), set()).add(nm)
        for ln, names in by_line.items():
            sub_lines.append({"name": ln, "target": len(names), "covered": len(names),
                              "rate": 100, "status": "done"})
    sub_cnt = len((sub or {}).get("stations", {}))

    categories = [
        {"key": "subway", "label": "地下鉄（福岡市地下鉄）",
         "source": "福岡市交通局（公式Excel）", "feed": "subway_timetable.json",
         "lastUpdate": (sub_update or "")[:10] or None,
         "target": sub_cnt, "covered": sub_cnt,
         "rate": 100 if sub_cnt else 0, "lines": sub_lines},
        {"key": "rail", "label": "在来線・新幹線・私鉄（福岡交通圏）",
         "source": "Yahoo!路線情報", "feed": "train_timetable.json",
         "lastUpdate": (tt_update or "")[:10] or None,
         "target": rail_target, "covered": rail_cov,
         "rate": round(rail_cov / rail_target * 100) if rail_target else 0,
         "lines": rail_lines},
    ]
    # ── 他都市の地下鉄（build_city_subway.py 産物）を自動で区分追加 ──
    for tp in sorted(glob.glob(os.path.join(HERE, "targets", "*_subway.json"))):
        slug = os.path.basename(tp).replace("_subway.json", "")
        tj = _load(tp) or {}
        tstations = tj.get("stations", [])
        oj = _load(os.path.join(OUT, f"{slug}_subway_timetable.json"))
        got = set((oj or {}).get("stations", {}).keys())
        by_line = {}
        for nm, st in (oj or {}).get("stations", {}).items():
            for sc in st.get("schedules", []):
                by_line.setdefault(sc.get("line_name", "?"), set()).add(nm)
        clines = [{"name": ln, "target": len(names), "covered": len(names),
                   "rate": 100, "status": "done"} for ln, names in sorted(by_line.items())]
        ct, cc = len(tstations), len([s for s in tstations if s["name"] in got])
        categories.append({
            "key": slug, "label": f"地下鉄（{tj.get('city', slug)}）",
            "source": "OSM＋Yahoo!路線情報", "feed": f"{slug}_subway_timetable.json",
            "lastUpdate": ((oj or {}).get("generated_at") or "")[:10] or None,
            "target": ct, "covered": cc,
            "rate": round(cc / ct * 100) if ct else 0, "lines": clines})

    tot_t = sum(c["target"] for c in categories)
    tot_c = sum(c["covered"] for c in categories)

    report = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc)
                       .replace(microsecond=0).isoformat(),
        "scope": "福岡交通圏（今後 都市別に拡張）",
        "overall": {"target": tot_t, "covered": tot_c,
                    "rate": round(tot_c / tot_t * 100) if tot_t else 0},
        "categories": categories,
        "history": [
            {"date": "2026-07-15", "text": "福岡市地下鉄 時刻表フィード 稼働（36駅）"},
            {"date": "2026-07-16", "text": "JR/西鉄 時刻表フィードの土台を追加（未生成）"},
            {"date": (tt_update or "")[:10] or "—",
             "text": "在来線・新幹線・私鉄 時刻表：未生成（train_timetable.json なし）"},
        ],
    }
    os.makedirs(OUT, exist_ok=True)
    json.dump(report, open(os.path.join(OUT, "coverage.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[done] 取得率 {report['overall']['rate']}%  "
          f"({tot_c}/{tot_t}駅)  → out/coverage.json")
    for c in categories:
        print(f"  {c['label']}: {c['covered']}/{c['target']} ({c['rate']}%)")
        for ln in c["lines"]:
            mark = {"done": "✅", "partial": "🔶", "todo": "⛔"}[ln["status"]]
            print(f"     {mark} {ln['name']}: {ln['covered']}/{ln['target']}")


if __name__ == "__main__":
    main()
