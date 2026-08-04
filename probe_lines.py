#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""都市を足す前の下調べ①：代表駅の**実際の路線名**をYahooから採る。

CONFIGS の line_patterns を推測で書くと、エラーにならずに **0件になる**。
（例：仙台は「仙台市営地下鉄」ではなく「仙台市地下鉄」／JRは全角「ＪＲ」／
　熊本市電の系統名は全角の「Ａ」「Ｂ」。半角で書くと1路線も一致しない）
必ずこの道具で実物を見てから書くこと。

使い方:
    python3 probe_lines.py <都道府県> <駅> [<駅> ...]
    python3 probe_lines.py 宮城県 仙台 長町南 泉中央

⚠️ ここでの同名駅よけは弱い（先頭候補しか見ない）ので、別の県の駅が出ることが
   ある。**それは道具の限界であって設定の誤りではない**。本番の取得(scrape_all)は
   候補を3件まで試し、駅ページの住所が REQUIRE_PREF と違えば捨てるので大丈夫。
   ここで見たいのは「路線名の文字列」だけ。

下調べ②（OSMの駅の種類を数える）＝ probe_osm.py
"""
import os
import sys
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))
import build_train as bt  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    pref, names = sys.argv[1], sys.argv[2:]
    bt.REQUIRE_PREF = pref
    for name in names:
        try:
            sid = bt.find_station_id(name)
            if not sid:
                print(f'  ✗ {name}: Yahooで見つからない')
                continue
            lines, addr = bt.get_station_page(sid)
            got = ' / '.join(sorted({ln.get('rail_name', '') for ln in lines}))
            warn = '' if addr.startswith(pref) else '  ⚠️別の県の駅を掴んでいる'
            print(f'  {name}（{sid}・{addr[:14]}）: {got}{warn}')
        except Exception as e:                    # 1駅こけても残りは続ける
            print(f'  ✗ {name}: {e}')
        time.sleep(2)                             # Yahooへの間隔（本番と同じ作法）
    return 0


if __name__ == '__main__':
    sys.exit(main())
