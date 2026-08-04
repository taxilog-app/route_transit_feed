#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""都市を足す前の下調べ②：その街に**どの種類の駅が何個あるか**をOSMから数える。

CONFIGS の overpass_filters と expected_stations を決めるための道具。
地下鉄が無い街は主役が変わる（モノレール／新交通／路面電車）ので、
実際に数えないと「0駅で成功」という静かな失敗になる。

実例（2026-08-03）:
  ・新潟の路面電車タグは1件ヒットするが中身は「新潟交通(株)新潟東部営業所」
    ＝**バス営業所の付け間違い**。拾うと存在しない駅が地図に出る。
  ・静岡・浜松・相模原・川崎は主役の乗り物が無い＝ターミナル駅だけで作る。

使い方:
    python3 probe_osm.py <市区町村> [<市区町村> ...]
    python3 probe_osm.py 仙台市 広島市 岡山市

🔴 Overpassの作法（守らないと必ず失敗する）
  ・`["railway"="station"]` のように**値を指定しない**問い合わせは planet 全体を
    なめてから範囲で絞る形になり、公開サーバーでは 504（時間切れ）になる。
    必ず key=value の形で聞くこと。
  ・同時に使える枠は**2つまで**。連投すると 429/504 が返る。この道具は
    1市1問い合わせにまとめ、市と市の間を20秒あけている。
"""
import collections
import json
import sys
import time
import urllib.parse
import urllib.request

ENDPOINTS = [
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
]
# 主役になりうる乗り物だけを、必ず key=value の形で聞く
FILTERS = [
    '["railway"="station"]["station"="subway"]',
    '["railway"="station"]["station"="monorail"]',
    '["railway"="station"]["station"="light_rail"]',
    '["railway"="tram_stop"]',
]
KIND = {'subway': '地下鉄', 'monorail': 'モノレール', 'light_rail': '新交通'}


def ask(query: str):
    for attempt in range(3):
        for ep in ENDPOINTS:
            try:
                req = urllib.request.Request(
                    ep, data=urllib.parse.urlencode({'data': query}).encode(),
                    headers={'User-Agent': 'route-timer-probe/1.0'})
                with urllib.request.urlopen(req, timeout=150) as r:
                    return json.load(r)
            except Exception as e:
                print(f'    [{ep.split("/")[2]}] {e}', file=sys.stderr)
        time.sleep(25)                            # 枠が空くのを待つ
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for city in sys.argv[1:]:
        nodes = ''.join(f'node(area.a){f};' for f in FILTERS)
        q = ('[out:json][timeout:150];'
             f'area["name"="{city}"]["admin_level"="7"]->.a;({nodes});out;')
        d = ask(q)
        print(f'■ {city}')
        if d is None:
            print('   取得失敗（時間をおいて再実行）')
            continue
        buckets = collections.defaultdict(set)
        for e in d.get('elements', []):
            t = e.get('tags') or {}
            if not t.get('name'):
                continue
            k = ('路面電車' if t.get('railway') == 'tram_stop'
                 else KIND.get(t.get('station'), 'その他'))
            buckets[k].add(t['name'])
        if not buckets:
            print('   （主役になる乗り物なし＝ターミナル駅だけで作る）')
        for k in sorted(buckets, key=lambda x: -len(buckets[x])):
            ns = sorted(buckets[k])
            tail = ' …' if len(ns) > 12 else ''
            print(f'   {k:6s} {len(ns):4d}  ' + '、'.join(ns[:12]) + tail)
        time.sleep(20)                            # 同時2枠の制限に当てない
    return 0


if __name__ == '__main__':
    sys.exit(main())
