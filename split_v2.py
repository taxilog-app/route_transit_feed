#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""時刻表を「軽い索引1本＋駅ごと1ファイル」に小分けする（v2フィード）。

【なぜ小分けするのか】
1つの都市を1ファイルにまとめると、大阪で8.3MB・東京は25MB超の見込みになる。
端末はそれを丸ごと解凍して抱え込むことになり、重い端末では落ちる。
運転手が実際に見るのは「近くの数駅」だけなので、

    索引（駅名・場所・路線名だけ＝数十KB）を1本
    ＋ 時刻表は見る駅のぶんだけ（1駅ぶん＝圧縮後で数KB）

に分ければ、都市の大きさに関係なく端末は軽いままでいられる。
（社長確定 2026-07-31。地理で10km等に切る案は「密な都市ほど効かない」ため不採用＝
  大阪は10km圏に91%の駅が入ってしまう。実測記録は指示メモ参照）

【入力】out/ にある v1 の1本もの（既存の作り方のまま・従来版も残す）
【出力】out/v2/<slug>/index.json と out/v2/<slug>/st/<駅名>.json、
        および出そろった都市の一覧 out/v2/index.json

v1（train_timetable.json 等）は**消さない**。古いアプリが入ったままの端末が
それを見に来ているため、当面は両方を並べて配る（アプリが v2 に入れ替わった後も
実害が無いので残す）。
"""
import hashlib
import json
import os
import sys
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))

OUT = "out"
V2 = os.path.join(OUT, "v2")

# 小分けの対象＝RailSchedule形式（train_timetable.json と同じ形）のフィード。
# 福岡市地下鉄(subway_timetable.json)は形式が別で0.36MBと軽いため対象外
# （小分けしても得が無く、アプリの別の器で読んでいるため触らない）。
SOURCES = [
    {"slug": "fukuoka", "file": "train_timetable.json", "label": "福岡（JR・西鉄）"},
    {"slug": "osaka", "file": "osaka_subway_timetable.json", "label": "大阪（地下鉄）"},
    {"slug": "sapporo", "file": "sapporo_subway_timetable.json", "label": "札幌（地下鉄）"},
    {"slug": "nagoya", "file": "nagoya_subway_timetable.json", "label": "名古屋（地下鉄）"},
    {"slug": "tokyo", "file": "tokyo_subway_timetable.json", "label": "東京（地下鉄）"},
]

# ファイル名に使えない文字（Windows/Pagesの両方で安全な範囲に寄せる）。
_UNSAFE = '/\\:*?"<>|'


def safe_name(name: str) -> str:
    s = "".join("_" if c in _UNSAFE else c for c in name).strip()
    return s or "_"


def station_hash(payload: dict) -> str:
    """駅の中身が前回と変わったかを見分ける印（8文字）。

    アプリはこの印を索引で見て、**印が変わった駅だけ**取り直す。
    ダイヤ改正で全駅が変わっても、変わらない駅は通信ゼロで済む。
    """
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:8]


def split_one(src):
    path = os.path.join(OUT, src["file"])
    if not os.path.exists(path):
        print(f"  – {src['file']}: まだ無い（この都市は飛ばす）")
        return None

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    stations = data.get("stations") or {}
    if not isinstance(stations, dict) or not stations:
        print(f"  ! {src['file']}: 駅が入っていない → 飛ばす", file=sys.stderr)
        return None

    slug = src["slug"]
    city_dir = os.path.join(V2, slug)
    st_dir = os.path.join(city_dir, "st")
    os.makedirs(st_dir, exist_ok=True)

    # 作り直しのたびに古い駅ファイルが残らないよう、いったん掃除する
    # （駅が廃止・改名された時に幽霊ファイルが配られ続けるのを防ぐ）。
    for old in os.listdir(st_dir):
        os.remove(os.path.join(st_dir, old))

    generated_at = data.get("generated_at") or time.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    index_stations = []
    used = {}
    total_bytes = 0

    for name, v in stations.items():
        if not isinstance(v, dict):
            continue
        schedules = v.get("schedules") or []
        # 索引に出す路線名（重複を消して並び順は元のまま＝画面の見出しに使う）
        lines = []
        for sc in schedules:
            ln = (sc.get("line_name") or "").strip()
            if ln and ln not in lines:
                lines.append(ln)

        fname = safe_name(name)
        # 万一ファイル名がぶつかったら連番で逃がす（別駅を上書きしない）
        if fname in used:
            used[fname] += 1
            fname = f"{fname}~{used[fname]}"
        else:
            used[fname] = 0

        station_doc = {
            "version": 2,
            "slug": slug,
            "name": name,
            "generated_at": generated_at,
            "attribution": data.get("attribution", ""),
            "lat": v.get("lat"),
            "lng": v.get("lng"),
            "city": v.get("city", ""),
            "schedules": schedules,
        }
        body = json.dumps(station_doc, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(st_dir, f"{fname}.json"), "w", encoding="utf-8") as f:
            f.write(body)
        total_bytes += len(body.encode("utf-8"))

        index_stations.append({
            "n": name,
            "f": f"{fname}.json",
            "lat": v.get("lat"),
            "lng": v.get("lng"),
            "c": v.get("city", ""),
            "l": lines,
            "v": station_hash({"schedules": schedules}),
        })

    index_doc = {
        "version": 2,
        "slug": slug,
        "label": src["label"],
        "city": data.get("city", ""),
        "generated_at": generated_at,
        "source": data.get("source", ""),
        "attribution": data.get("attribution", ""),
        "station_path": "st",
        "stations": index_stations,
    }
    ipath = os.path.join(city_dir, "index.json")
    with open(ipath, "w", encoding="utf-8") as f:
        json.dump(index_doc, f, ensure_ascii=False, separators=(",", ":"))

    ikb = os.path.getsize(ipath) / 1024
    n = len(index_stations)
    print(f"  ✓ {slug}: {n}駅 索引{ikb:.0f}KB / 駅ファイル計{total_bytes/1024/1024:.2f}MB "
          f"（1駅平均{total_bytes/n/1024:.0f}KB）")
    return {
        "slug": slug,
        "label": src["label"],
        "city": data.get("city", ""),
        "generated_at": generated_at,
        "stations": n,
        "index": f"{slug}/index.json",
    }


def main():
    if not os.path.isdir(OUT):
        print("out/ が無い。先に build_*.py を回すこと", file=sys.stderr)
        sys.exit(1)
    os.makedirs(V2, exist_ok=True)
    print("時刻表を索引＋駅ごとに小分け中…")
    cities = [c for c in (split_one(s) for s in SOURCES) if c]
    if not cities:
        print("[GUARD] 小分けできた都市が1つも無い → 異常", file=sys.stderr)
        sys.exit(1)
    with open(os.path.join(V2, "index.json"), "w", encoding="utf-8") as f:
        json.dump({
            "version": 2,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
            "cities": cities,
        }, f, ensure_ascii=False, separators=(",", ":"))
    print(f"📝 out/v2/index.json（{len(cities)}都市）")


if __name__ == "__main__":
    main()
