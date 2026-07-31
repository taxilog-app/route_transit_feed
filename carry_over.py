#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""今回作らなかった都市の時刻表を、公開中の棚から回収して持ち越す。

【なぜ要るのか】
棚（GitHub Pages）は「今回アップしたもので丸ごと置き換わる」。
そのため、福岡だけ作り直した回にそのまま配ると、**大阪・札幌・名古屋・東京が
棚から消える**（＝運転手の端末から時刻表が無くなる）。

そこで配る直前に、今回作らなかった都市ぶんを公開中の棚から1本ずつ回収して
out/ に戻す。回収するのは1都市1ファイル（従来の1本もの）だけで、
そこから split_v2.py が索引と駅ごとファイルを作り直す。

回収に失敗した都市があれば **配信を中止する**（中途半端に消すより、前回のまま
置いておく方が安全）。
"""
import os
import sys
import urllib.request

os.chdir(os.path.dirname(os.path.abspath(__file__)))

PAGES = "https://taxilog-app.github.io/route_transit_feed"
UA = "route-timer-app feed builder (taxilog-app) carry_over.py"

# 棚に並んでいるべき1本もの（v1）。ここに無い都市は持ち越し対象外。
FILES = [
    "subway_timetable.json",          # 福岡市地下鉄（公式Excel）
    "train_timetable.json",           # 福岡 JR・西鉄
    "osaka_subway_timetable.json",
    "sapporo_subway_timetable.json",
    "nagoya_subway_timetable.json",
    "tokyo_subway_timetable.json",
]

# 最低限これだけは棚に無いと異常（＝配信中止）。
# 新しい都市を足したばかりの回は「まだ棚に無い」が正常なので、必須には入れない。
REQUIRED = {"subway_timetable.json", "train_timetable.json"}


def fetch(name):
    req = urllib.request.Request(f"{PAGES}/{name}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main():
    os.makedirs("out", exist_ok=True)
    missing_required = []
    for name in FILES:
        path = os.path.join("out", name)
        if os.path.exists(path):
            print(f"  = {name}: 今回作った分を使う")
            continue
        try:
            body = fetch(name)
        except Exception as e:
            if name in REQUIRED:
                missing_required.append((name, str(e)))
                print(f"  ! {name}: 回収できない（{e}）", file=sys.stderr)
            else:
                print(f"  – {name}: 棚にまだ無い（この都市は今回も無し）")
            continue
        with open(path, "wb") as f:
            f.write(body)
        print(f"  ↓ {name}: 公開中の棚から回収（{len(body)/1024/1024:.2f}MB）")

    if missing_required:
        print(f"[GUARD] 必須の {missing_required} が回収できない → 配信中止"
              "（前回の棚をそのまま残す）", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
