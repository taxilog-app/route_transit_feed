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

🔴【2026-08-15 追加】棚から消えた都市を「無かったこと」にしない
  それまでは、必須2ファイル以外は回収に失敗しても
  「棚にまだ無い（この都市は今回も無し）」と印字して**黙って先へ進んで**いた。
  ねらいは「新しく足したばかりの都市はまだ棚に無いのが正常」を通すこと。
  ところが同じ道を「一瞬つながらなかっただけの都市」も通ってしまう。
  一度でもそこを通ると、その都市は棚から消え、次からは本当に棚に無いので
  **毎回同じ道を通り続ける＝二度と戻らない**。しかも誰にも通知されない。

  実害（2026-08-04〜08-15）: 長崎（40駅）が棚から消え、**11日間**気づかれず、
  長崎本土地区の運転手には時刻表が出ないままだった。

  対策＝**前回の棚に載っていた都市は、必ず今回も載せる**。
  「前回の棚」は公開中の v2/index.json 自身が知っている（そこに並んでいる
  都市＝運転手が今使えている都市）。1つでも欠けたら配信を中止する。
  これで「まだ足していない新しい都市」は通し、「前はあったのに消えた都市」は
  止まる、という取り違えの起きない線引きになる。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

os.chdir(os.path.dirname(os.path.abspath(__file__)))

PAGES = "https://taxilog-app.github.io/route_transit_feed"
UA = "route-timer-app feed builder (taxilog-app) carry_over.py"

# 一瞬の通信の失敗で都市を落とさないよう、取り直す（間を空けて3回）。
RETRIES = 3
RETRY_WAIT = 5  # 秒

# 棚に並んでいるべき1本もの（v1）。ここに無い都市は持ち越し対象外。
# 🔴 都市名を直書きしない。名簿は CONFIGS（build_city_subway.py）ひとつ。
#    ここが古いと、その都市は「棚から拾い直されない」＝配信のたびに消える。
from build_city_subway import CONFIGS as _CITY_CONFIGS  # noqa: E402

# v2の索引に出る都市名(slug) → 1本ものファイル名。福岡だけ出どころが別
# （JR・西鉄を build_train.py が作る）＝ split_v2.py の SOURCES と同じ対応。
FILE_BY_SLUG = {"fukuoka": "train_timetable.json"}
FILE_BY_SLUG.update({slug: f"{slug}_subway_timetable.json" for slug in _CITY_CONFIGS})

FILES = [
    "subway_timetable.json",          # 福岡市地下鉄（公式Excel）
    "train_timetable.json",           # 福岡 JR・西鉄
    # 🔴 フェリーも必ず回収する（2026-08-13 追加）。ここに書かないと、電車だけを
    #    作り直した回に**フェリーが棚から消える**（棚は丸ごと置き換わるため）。
    #    フェリーは別ワークフロー(ferry.yml)で作るので、電車側の回でここが効く。
    "ferry_timetable.json",           # 福岡市営渡船（到着）
] + [f"{slug}_subway_timetable.json" for slug in _CITY_CONFIGS]

# 最低限これだけは棚に無いと異常（＝配信中止）。
# 新しい都市を足したばかりの回は「まだ棚に無い」が正常なので、必須には入れない。
REQUIRED = {"subway_timetable.json", "train_timetable.json"}


def fetch(name):
    """棚から1本取る。一瞬の失敗で諦めない（RETRIES回まで取り直す）。"""
    last = None
    for i in range(RETRIES):
        try:
            req = urllib.request.Request(f"{PAGES}/{name}",
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            # 404＝本当に棚に無い。取り直しても変わらないので即座に返す。
            if e.code == 404:
                raise
            last = e
        except Exception as e:  # 通信断・タイムアウト等は取り直す
            last = e
        if i < RETRIES - 1:
            print(f"    …{name}: 取り直します（{last}）", file=sys.stderr)
            time.sleep(RETRY_WAIT)
    raise last


def published_city_slugs():
    """公開中の棚に今並んでいる都市(slug)＝運転手が今使えている都市。

    取れなければ None（＝前回の棚が読めない）。読めないまま配ると
    何を消したのか分からないので、呼び出し側で配信を中止する。
    """
    try:
        body = fetch("v2/index.json")
        data = json.loads(body.decode("utf-8"))
        return [c["slug"] for c in data.get("cities", []) if c.get("slug")]
    except Exception as e:
        print(f"  ! v2/index.json が読めない（{e}）", file=sys.stderr)
        return None


def main():
    os.makedirs("out", exist_ok=True)

    # 先に「前回の棚に載っていた都市」を控える（＝今回も必ず載せるべき名簿）。
    before = published_city_slugs()

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

    # 🔴 前回の棚に載っていた都市が1つでも欠けたら配信しない。
    #    黙って減らすと運転手の時刻表が消え、しかも誰も気づけない（長崎の実害）。
    if before is None:
        print("[GUARD] 前回の棚（v2/index.json）が読めない → 配信中止"
              "（何を消すことになるのか確かめられないため）", file=sys.stderr)
        sys.exit(1)

    dropped = []
    for slug in before:
        f = FILE_BY_SLUG.get(slug)
        if f is None:
            # 棚にはあるが今の名簿(CONFIGS)に無い＝都市を消す改修をした時。
            # 意図的な削除まで止めない（名簿から消したのは人の判断）。
            print(f"  ⚠ {slug}: 棚にはあるが今の名簿に無い（名簿から外した都市とみなす）")
            continue
        if not os.path.exists(os.path.join("out", f)):
            dropped.append(slug)

    if dropped:
        print(f"[GUARD] 前回の棚にあった {len(dropped)}都市 {dropped} が今回そろわない "
              "→ 配信中止（前回の棚をそのまま残す）。"
              "その都市を build-city で作り直してから配ること。", file=sys.stderr)
        sys.exit(1)

    print(f"  ✅ 前回の棚の{len(before)}都市はすべて今回もそろっています")


if __name__ == "__main__":
    main()
