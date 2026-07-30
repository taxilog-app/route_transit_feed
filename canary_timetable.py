#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_transit_feed — 時刻表カナリア（指示書D）。

いま build_train.py は毎月1日、対象駅すべて(Yahoo・2秒スロットルで約15分)を
まるごと取り直している。だがダイヤ改正は年1〜2回（主に3月）しか起きない。
このスクリプトは **代表駅だけ**(約1分)を毎日取得し、公開中の
train_timetable.json と突き合わせて「変わったらしい」を検知する。
変化が無ければフル取得(15分)を省き、変化があった日だけ build_train.py を
呼び出す判断材料にする（呼び出し自体は canary.yml 側で行う）。

スクレイプ処理そのもの（HTTP取得・__NEXT_DATA__の読み方）は書き直さない。
build_train.py の fetch / find_station_ids / get_lines_at_station /
get_timetable をそのまま `import build_train` で呼ぶ。別の読み方をすると
「カナリアは通るのに本番取得は壊れている」が起きるため。

ただし build_train.scrape_all() をそのまま流用すると、1駅につき
(平日/土曜/日祝)×路線数ぶんの時刻表取得が発生し、代表10駅では実測で
数分〜かかってしまい「約1分」に収まらない（実測: scrape_all全面採用で
博多1駅だけでも33秒＝14リクエスト）。判定に要るのは「変わったか」だけなので、
ここでは平日ダイヤ(kind=1)だけを取る（ダイヤ改正は平日/土曜/日祝が同時に
変わるのが通常で、平日だけの比較で十分。仮に土日だけの改定を見逃しても
月1のbuild.ymlが安全網として拾う）。この軽量化で実測10駅=108秒（≒1分48秒。
「1分弱」の目安よりは少し長いが、THROTTLEは短くしない方針のため許容）。
駅の候補選定(同名駅の順に試す)は
build_train.scrape_all()と同じロジックをそのまま踏襲する。

代表駅の選び方（stations_target.json に実在する駅から選定。路線ごとに
最低1駅、主要路線は2駅で冗長化。10駅前後に収める）:
  博多           … kagoshima / shinkansen_sanyo / shinkansen_kyushu（起点駅）
  香椎           … kagoshima / kashii
  吉塚           … kagoshima / sasaguri（福北ゆたか線側）
  古賀           … kagoshima（郊外側の区間も見る）
  香椎神宮       … kashii（2駅目）
  篠栗           … sasaguri（2駅目・篠栗線側）
  下山門         … chikuhi
  九大学研都市   … chikuhi（2駅目）
  西鉄二日市     … nishitetsu_omuta / nishitetsu_dazaifu
  西鉄福岡(天神) … nishitetsu_omuta（2駅目・起点駅）
これで stations_target.json の全8路線キーを一通りカバーする。

判定: 上記駅の平日ダイヤ(departures)が、公開フィードの同じ駅・同じ路線と
1つでも違えば「変化あり」。順序違いなど無意味な差分を拾わないよう
(line_key, rail_id) をキーにして比較する（誤検知を減らすだけで、本文の
判定基準そのものは変えない）。

⚠️ 実測で確認済み: 同じ日に2回取っても中身は1本もズレない（取得は安定して
   いる）。一方、公開フィード(2026-07-17生成)と現在を比べると、同じ路線
   (rail_id=2351)でも `direction` の表示文字列が「鳥栖・武雄温泉」→
   「鳥栖・久留米」に変わっていた。つまり direction は月をまたぐと変わりうる
   ＝比較キーには向かない。rail_id(groupId)は変わっていなかったので
   こちらをキーにする。

⚠️ 誤検知しても損は15分だけ。逆に見逃すと古い時刻表が出続ける。
   迷ったら「変化あり」に倒す。公開フィードにその駅が無い／取得できない
   場合も「変化あり」扱い（安全側）。

終了コード:
  0  = 変化なし（フル取得は不要）
  10 = 変化あり（改正の疑い。呼び出し側で build_train.py を回すこと）
  1  = 取得に失敗（1駅も取得できない＝壊れている。health_check.py と同じ意味）
"""
import json
import os
import re
import sys
import time
import urllib.request

import build_train

UA = build_train.UA
FEED_URL = build_train.FEED_URL
STATIONS_TARGET_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "stations_target.json")
CANARY_KIND = 1  # 平日ダイヤのみ（kind: 1=平日 2=土曜 4=日祝、build_train.pyと同じ対応）

CANARY_STATION_NAMES = [
    "博多", "香椎", "吉塚", "古賀", "香椎神宮",
    "篠栗", "下山門", "九大学研都市", "西鉄二日市", "西鉄福岡(天神)",
]


def load_canary_targets():
    with open(STATIONS_TARGET_PATH, encoding="utf-8") as f:
        all_stations = json.load(f)["stations"]
    by_name = {s["name"]: s for s in all_stations}
    missing = [n for n in CANARY_STATION_NAMES if n not in by_name]
    if missing:
        raise RuntimeError(f"stations_target.json に存在しない駅名: {missing}")
    return [by_name[n] for n in CANARY_STATION_NAMES]


def scrape_canary_station(station):
    """station: stations_target.json の1エントリ。

    build_train.scrape_all() の候補選定ロジック(同名駅の候補を最大3件まで
    順に試す)をそのまま踏襲しつつ、時刻表は平日ダイヤ(CANARY_KIND)だけを
    取る軽量版。失敗時は例外を投げる（呼び出し側でfailedに積む）。
    """
    name = station["name"]
    target_keys = set(station["lines"])

    candidate_ids = build_train.find_station_ids(name, query=name)
    time.sleep(build_train.THROTTLE)
    if not candidate_ids:
        raise RuntimeError("駅IDが見つからない(noid)")

    sid, matched = None, []
    for cid in candidate_ids[:3]:
        lines = build_train.get_lines_at_station(cid)
        time.sleep(build_train.THROTTLE)
        cand_matched, cand_seen_rail = [], set()
        for line in lines:
            for key in target_keys:
                if any(re.search(p, line["rail_name"])
                       for p in build_train.LINE_PATTERNS.get(key, [])):
                    if line["rail_id"] not in cand_seen_rail:
                        cand_seen_rail.add(line["rail_id"])
                        cand_matched.append({**line, "line_key": key})
                    break
        if cand_matched:
            sid, matched = cid, cand_matched
            break
    if not matched:
        raise RuntimeError("対象路線が見つからない(noroute)")

    schedules = []
    for ml in matched:
        tt = build_train.get_timetable(sid, ml["rail_id"], CANARY_KIND)
        time.sleep(build_train.THROTTLE)
        schedules.append({
            "line_key": ml["line_key"], "rail_id": ml["rail_id"],
            "departures": tt["departures"],
        })
    return {"station_id": sid, "schedules": schedules}


def scrape_all_canary(targets):
    fresh, failed = {}, []
    for st in targets:
        name = st["name"]
        try:
            fresh[name] = scrape_canary_station(st)
        except Exception as e:
            failed.append((name, str(e)))
            continue
        print(f"  {name}: {len(fresh[name]['schedules'])}スケジュール")
    return fresh, failed


def fetch_published_stations():
    """公開中フィードの stations を返す。取れなければ None（＝安全側で変化ありにする）。"""
    try:
        req = urllib.request.Request(FEED_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("stations") or {}
    except Exception as e:
        print(f"[WARN] 公開フィード取得に失敗: {e}", file=sys.stderr)
        return None


def canonical_schedules(station_data):
    """順序違いを拾わないための比較用の形（中身の判定基準は変えない）。

    キーは (line_key, rail_id)。公開フィード側は build_train.py が全kind
    (weekday/saturday/holiday)を持つので、カナリアが取った平日ダイヤ
    (weekday)だけを抜き出して同じキーで突き合わせる。
    """
    out = {}
    for sch in station_data.get("schedules", []):
        if sch.get("day_type") not in (None, "weekday"):
            continue
        key = (sch.get("line_key"), sch.get("rail_id"))
        out[key] = sch.get("departures")
    return out


def main():
    dry_run = "--dry-run" in sys.argv

    targets = load_canary_targets()
    if dry_run:
        targets = targets[:1]
        print(f"[DRY-RUN] {targets[0]['name']} のみ取得します")

    print(f"カナリア: 代表{len(targets)}駅（平日ダイヤのみ）を取得…")
    start = time.time()
    fresh, failed = scrape_all_canary(targets)
    elapsed = time.time() - start
    print(f"取得完了（{elapsed:.0f}秒）: 成功{len(fresh)}駅 / 失敗{len(failed)}件")
    for name, err in failed:
        print(f"  [FAIL] {name}: {err}", file=sys.stderr)

    if not fresh:
        print("[判定] 1駅も取得できませんでした → 取得失敗（壊れている）", file=sys.stderr)
        sys.exit(1)

    published = fetch_published_stations()
    if published is None:
        print("[判定] 公開フィードが読めない → 安全側で変化ありとする")
        sys.exit(10)

    checked_names = [t["name"] for t in targets]
    changed = []
    for name in checked_names:
        if name not in fresh:
            changed.append(f"{name}（取得できず・安全側で変化あり扱い）")
            continue
        prev = published.get(name)
        if prev is None:
            changed.append(f"{name}（公開フィードに無い）")
            continue
        if canonical_schedules(fresh[name]) != canonical_schedules(prev):
            changed.append(name)

    if dry_run:
        print(f"[DRY-RUN] 比較結果: {'差分あり - ' + ', '.join(changed) if changed else '一致'}")

    if changed:
        print(f"[判定] 変化あり: {', '.join(changed)}")
        sys.exit(10)

    print("[判定] 変化なし")
    sys.exit(0)


if __name__ == "__main__":
    main()
