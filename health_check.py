#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_transit_feed — Yahoo!路線情報 壊れ検知（カナリア）。

目的:
  アプリ (lib/services/train_service.dart) は Yahoo!路線情報のHTMLを読んで
  「平常運転 / 遅延 / 運転見合わせ」を表示している。Yahoo側のページの作りが
  変わると、読み取りが黙って壊れて「平常運転」に化ける＝運転手が見合わせに
  気づけない。これを *早く* 知るために、毎日ここで同じ読み方を試す。

判定（アプリ側 _parseIndividual と同じ条件にすること）:
  1. HTTP 200 で取れるか（URL変更・404の検出）
  2. `<div id="mdServiceStatus">` ブロックが在るか（HTML構造の変更を検出）
  3. その中の最初の `<dt>` が既知の状態語に当たるか（表記変更を検出）
  4. エリアページ area/7 が期待の形か（表が在る or 平常時の定型文が在る）

終了コード:
  0 = 正常 / 1 = 壊れている（過半が読めない・全滅）
  GitHub Actions の定期実行が失敗するとリポジトリ所有者にメールが飛ぶ。
  これが「変動費0円の通知」。追加のサービス契約は不要。

費用: GitHub Actions（publicリポジトリ）は無料枠。1日12リクエストのみ。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

UA = "route_transit_feed/0.1 (+https://github.com/taxilog-app/route_transit_feed)"
THROTTLE = float(os.environ.get("THROTTLE", "2.0"))  # 礼儀: 2秒以上あける
TIMEOUT = 20

AREA_URL = "https://transit.yahoo.co.jp/diainfo/area/7"

# ⚠️ アプリ lib/services/train_service.dart の _individualTargets と同じ内容にする。
#    片方だけ増減させると壊れ検知が本番とズレる。
INDIVIDUAL_TARGETS = [
    ("https://transit.yahoo.co.jp/diainfo/8/0", "山陽新幹線"),
    ("https://transit.yahoo.co.jp/diainfo/410/0", "九州新幹線"),
    ("https://transit.yahoo.co.jp/diainfo/413/0", "西鉄天神大牟田線"),
    ("https://transit.yahoo.co.jp/diainfo/386/386", "鹿児島本線"),
    ("https://transit.yahoo.co.jp/diainfo/389/0", "福北ゆたか線"),
    ("https://transit.yahoo.co.jp/diainfo/392/0", "香椎線"),
    ("https://transit.yahoo.co.jp/diainfo/522/0", "筑肥線"),
    ("https://transit.yahoo.co.jp/diainfo/397/0", "空港線"),
    ("https://transit.yahoo.co.jp/diainfo/398/0", "箱崎線"),
    ("https://transit.yahoo.co.jp/diainfo/409/0", "七隈線"),
    ("https://transit.yahoo.co.jp/diainfo/416/0", "西鉄貝塚線"),
]

# アプリ _mapStatus と同じ語彙
STATUS_WORDS = {
    "normal": ["平常運転", "通常運行"],
    "suspension": ["運転見合わせ", "運休", "運転中止"],
    "delay": ["遅延", "ダイヤが乱れ", "運転状況", "運転計画"],
}

BLOCK_RE = re.compile(r'<div id="mdServiceStatus".*?</div>', re.DOTALL)
DT_RE = re.compile(r"<dt[^>]*>(.*?)</dt>", re.DOTALL)
TAG_RE = re.compile(r"<[^>]*>")


def fetch(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return res.status, res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # 通信断・タイムアウト
        print(f"    fetch error: {e}", file=sys.stderr)
        return 0, ""


def map_status(text):
    """アプリ _mapStatus と同じ判定。当たらなければ None（＝表記が変わった疑い）。"""
    for state, words in STATUS_WORDS.items():
        if any(w in text for w in words):
            return state
    return None


def check_individual(url, name):
    code, html = fetch(url)
    r = {"name": name, "url": url, "http": code,
         "block_found": False, "status": None, "ok": False}
    if code != 200 or not html:
        r["reason"] = f"取得できない（HTTP {code}）"
        return r
    block = BLOCK_RE.search(html)
    if not block:
        r["reason"] = "運行情報ブロック(mdServiceStatus)が無い＝HTMLの作りが変わった"
        return r
    r["block_found"] = True
    dt = DT_RE.search(block.group(0))
    if not dt:
        r["reason"] = "ブロック内に状態表記(<dt>)が無い"
        return r
    label = TAG_RE.sub(" ", dt.group(1)).strip()
    status = map_status(label)
    r["label"] = label[:40]
    r["status"] = status
    if status is None:
        r["reason"] = f"状態の言い回しが変わった（読めた文字＝「{label[:20]}」）"
        return r
    r["ok"] = True
    return r


def check_area():
    code, html = fetch(AREA_URL)
    r = {"name": "九州エリア一覧", "url": AREA_URL, "http": code, "ok": False}
    if code != 200 or not html:
        r["reason"] = f"取得できない（HTTP {code}）"
        return r
    # 平常時は表が空なのが正常。表の有無ではなく「ページの目印」で形を確かめる。
    has_table = "<table" in html
    sentinel = ("情報はありません" in html) or ("平常運転" in html) or ("運行情報" in html)
    r["ok"] = has_table or sentinel
    if not r["ok"]:
        r["reason"] = "一覧ページの目印が無い＝ページの作りが変わった"
    return r


def main():
    print("Yahoo!路線情報 壊れ検知（カナリア）")
    results = []
    for url, name in INDIVIDUAL_TARGETS:
        res = check_individual(url, name)
        mark = "OK " if res["ok"] else "NG "
        print(f"  {mark} {name}: {res.get('status') or res.get('reason')}")
        results.append(res)
        time.sleep(THROTTLE)

    area = check_area()
    print(f"  {'OK ' if area['ok'] else 'NG '} {area['name']}: "
          f"{'正常' if area['ok'] else area.get('reason')}")

    total = len(results)
    ok = sum(1 for r in results if r["ok"])
    blocks = sum(1 for r in results if r["block_found"])
    ng = [r for r in results if not r["ok"]]

    # アプリ _updateHealth と同じ倒し方：過半が読めなければ壊れている扱い。
    if ok == 0:
        state, reason = "broken", "路線ごとの運行情報を1件も読めませんでした"
    elif (total - blocks) * 2 > total:
        state, reason = "broken", "運行情報ブロックが無いページが過半＝HTMLの作りが変わった"
    elif ok * 2 <= total:
        state, reason = "broken", "読めない路線が過半"
    elif ng or not area["ok"]:
        state, reason = "degraded", "一部が読めない（" + \
            "・".join([r["name"] for r in ng] + ([] if area["ok"] else ["一覧ページ"])) + "）"
    else:
        state, reason = "ok", "正常"

    report = {
        "state": state, "reason": reason,
        "ok": ok, "total": total, "block_found": blocks,
        "area_ok": area["ok"],
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "lines": results, "area": area,
    }
    os.makedirs("out", exist_ok=True)
    with open("out/train_health.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    summary = (f"## 電車運行情報の壊れ検知: **{state}**\n\n"
               f"- 判定: {reason}\n- 読めた路線: {ok}/{total}\n"
               f"- 一覧ページ: {'正常' if area['ok'] else '異常'}\n")
    if ng:
        summary += "\n### 読めなかった路線\n" + "".join(
            f"- {r['name']}: {r.get('reason')}\n" for r in ng)
    print("\n" + summary)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(summary)

    if state == "broken":
        print("::error::電車運行情報の読み取りが壊れています。"
              "アプリは『平常運転』と表示せず取得エラーを出しますが、"
              "train_service.dart の解析を直す必要があります。")
        return 1
    if state == "degraded":
        print("::warning::一部の路線が読めていません（アプリは表示を続けます）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
