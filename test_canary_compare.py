#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指示書D §5-2 の「くらべる試験」。

カナリアの“判定部分だけ”を検証する。Yahooには一切アクセスしない
（取得と公開フィードの読み込みを差し替えて、比較ロジックだけを動かす）。

実物どおりの形で試す：
  ・カナリアが取る側 = 平日だけ・day_type を持たない（scrape_canary_station の出力）
  ・公開フィード側   = 平日/土曜/日祝を全部持つ（build_train.py の出力）
  この非対称を正しく突き合わせられるかが肝。

確認すること
  1. 前回とまったく同じ            → 0（変化なし＝15分のフル取得に進まない）
  2. 平日の発車時刻が1つ違う        → 10（変化あり）
  3. 平日の本数が1本増えた          → 10
  4. 路線がまるごと増えた           → 10
  5. 土曜だけ違う                   → 0（カナリアは平日しか見ない＝仕様どおり）
  6. 公開フィードにその駅が無い     → 10（安全側）
  7. 公開フィードが読めない         → 10（安全側）
  8. 1駅も取得できない              → 1（壊れている）
"""
import copy
import os
import io
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import canary_timetable as C  # noqa: E402


def fresh_data():
    """カナリアが取ってくる形（平日のみ・day_type なし）。"""
    return {
        '博多': {'station_id': '1', 'schedules': [
            {'line_key': 'kagoshima', 'rail_id': 'r1',
             'departures': ['05:12', '05:30', '06:01']},
            {'line_key': 'shinkansen_sanyo', 'rail_id': 'r2',
             'departures': ['06:00', '06:20']},
        ]},
        '香椎': {'station_id': '2', 'schedules': [
            {'line_key': 'kashii', 'rail_id': 'r3',
             'departures': ['05:20', '05:48']},
        ]},
    }


def published_data():
    """公開フィードの形（平日・土曜・日祝を全部持つ）。"""
    return {
        '博多': {'schedules': [
            {'day_type': 'weekday', 'line_key': 'kagoshima', 'rail_id': 'r1',
             'departures': ['05:12', '05:30', '06:01']},
            {'day_type': 'saturday', 'line_key': 'kagoshima', 'rail_id': 'r1',
             'departures': ['05:40', '06:10']},
            {'day_type': 'holiday', 'line_key': 'kagoshima', 'rail_id': 'r1',
             'departures': ['05:45']},
            {'day_type': 'weekday', 'line_key': 'shinkansen_sanyo', 'rail_id': 'r2',
             'departures': ['06:00', '06:20']},
        ]},
        '香椎': {'schedules': [
            {'day_type': 'weekday', 'line_key': 'kashii', 'rail_id': 'r3',
             'departures': ['05:20', '05:48']},
            {'day_type': 'saturday', 'line_key': 'kashii', 'rail_id': 'r3',
             'departures': ['05:55']},
        ]},
    }


def run_case(name, fresh, published, expect):
    C.load_canary_targets = lambda: [{'name': n} for n in ('博多', '香椎')]
    C.scrape_all_canary = lambda targets: (fresh, [])
    C.fetch_published_stations = lambda: published
    buf = io.StringIO()
    code = None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            C.main()
    except SystemExit as e:
        code = e.code
    ok = code == expect
    verdict = [l for l in buf.getvalue().splitlines() if '[判定]' in l]
    print(f"{'✓' if ok else '✗ 失敗'} {name}: 終了コード {code}（期待 {expect}）"
          f"{'  ' + verdict[0] if verdict else ''}")
    return ok


def main():
    print('=== カナリアの「前回と変わったか」判定の試験（Yahoo未使用）===\n')
    r = []

    r.append(run_case('前回とまったく同じ', fresh_data(), published_data(), 0))

    f = fresh_data()
    f['博多']['schedules'][0]['departures'][1] = '05:33'
    r.append(run_case('平日の発車時刻が1つ変わった', f, published_data(), 10))

    f = fresh_data()
    f['博多']['schedules'][0]['departures'].append('06:15')
    r.append(run_case('平日の本数が1本増えた', f, published_data(), 10))

    f = fresh_data()
    f['香椎']['schedules'].append(
        {'line_key': 'kagoshima', 'rail_id': 'r9', 'departures': ['07:00']})
    r.append(run_case('路線がまるごと増えた', f, published_data(), 10))

    p = published_data()
    p['博多']['schedules'][1]['departures'] = ['09:99']  # 土曜だけ変える
    r.append(run_case('土曜だけ違う（平日しか見ない仕様）', fresh_data(), p, 0))

    p = published_data()
    del p['香椎']
    r.append(run_case('公開フィードに駅が無い', fresh_data(), p, 10))

    r.append(run_case('公開フィードが読めない', fresh_data(), None, 10))

    r.append(run_case('1駅も取得できない', {}, published_data(), 1))

    print(f"\n結果: {sum(r)}/{len(r)} 件 合格")
    return 0 if all(r) else 1


if __name__ == '__main__':
    sys.exit(main())
