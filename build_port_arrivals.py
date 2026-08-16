#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""港の到着（クルーズ客船・離島航路）のフィードを作る。

【何のためか】
港はタクシー需要の山。**船が着く時刻に先回りできる**のが狙い。
市営渡船（build_ferry.py）とは出どころも形も違うので、道具もファイルも分ける。

【🔴 なぜ ferry_timetable.json に足さないのか】
アプリ側のフィード検査は**出力項目の完全一致**を見る。既存ファイルに項目を1つ
足すと、古いアプリでは配信が丸ごと止まる（gotcha-feed-schema-exact-key-check）。
市営渡船は既に運転手の端末で動いているので、**触らない**。別ファイルにする。

【🔴 会社ごとにオン/オフできること（社長判断 2026-08-16 の前提）】
壱岐対馬・五島は運航会社へ事前照会せずに載せる、という判断をしている。
その代わりの保険が `enabled`。先方から連絡が来たら、その会社の enabled を
false にして配り直せば**その航路だけ**消える。アプリの作り直しにしない。

【出どころと許諾】
  ① クルーズ客船 … 福岡市 港湾空港局 港湾振興部 旅客振興課
     🎯 2026-08-16 メールで**利用許諾を取得済み**（事実情報のみ・出典明記が条件）。
     🔴 取得は**週1回以上**（市の指摘。直前の時刻変更・中止・追加が頻繁で、
        市も数日に1回ページを更新する）。月1回では嘘の時刻を出す。
  ② フェリー太古（野母商船）… 公式サイトの時刻表（HTML）。
     利用規約ページが存在せず著作権表記のみ。事実（時刻）だけを出典付きで出す。
  ③ 九州郵船（壱岐・対馬）… 公式サイトの**ダイヤ検索**（日付を渡すと時刻が返る）。
     🔴 PDFは読まない。検索は配船パターンA/B/Cを解決済みの時刻を返すため。
        2026-08-16 にPDFと突き合わせ、パターンの切り替わり日（8/16 B → 8/17 A）
        まで含めて完全一致を確認した。

【🔴 ここに入っていないもの＝当日の欠航】
  時化などの当日欠航は、九州郵船・野母商船・福岡市営渡船のいずれも自社サイトに
  載せず「九州のりものinfo.com」へ案内している。そのサイトは robots.txt で
  データ掲載ページを Disallow しており、**機械での取得を断っている**。
  2026-08-16 に運営協議会へ照会中。回答が出るまで欠航は扱わない。
  ＝**このフィードの時刻は「予定」であって「今日動いているか」ではない。**

【まだ入っていないもの】
  ・カメリアライン（釜山・博多着7:30）＝1日1便。手入力で足りる。
  ・海中ライン（安田産業汽船・博多ふ頭第1着・日4〜12便）＝未検討。

使い方:
    python3 build_port_arrivals.py            # 出力 out/port_arrivals.json
    python3 build_port_arrivals.py --check    # 作らずに検査だけ
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out", "port_arrivals.json")
JST = dt.timezone(dt.timedelta(hours=9))
UA = "route-timer-app feed builder (taxilog-app) build_port_arrivals.py"

# ── クルーズ客船（福岡市） ──────────────────────────────────────────────
CRUISE_URL = ("https://www.city.fukuoka.lg.jp/kowan/k-kikaku/"
              "hakata-port/cruise1.html")
CRUISE_ATTRIBUTION = ("出典：福岡市 港湾空港局 港湾振興部 旅客振興課"
                      "「クルーズ客船寄港予定」")
# 🔴 この文言は市が「この書き方で問題ございません」と認めたもの（2026-08-16）。
#    勝手に言い換えない。利用条件＝福岡市「リンク・著作権について」
#    https://www.city.fukuoka.lg.jp/sub/tyosakuken.html

# クルーズ船が着く岸壁は中央ふ頭と箱崎ふ頭。
# 🔴 座標は「建物」ではなく**タクシー乗り場**を入れる（運転手が向かう先だから）。
#    中央ふ頭＝社長（福岡の現役運転手）が地図上で指定した乗り場 2026-08-16。
#    OSMには「中央ふ頭クルーズセンター」という名前の物が無く、機械では出せない。
#    現地を知っている人の指定が最も正確なので、これを正とする。
CRUISE_PORTS = {
    "中央": {"name": "中央ふ頭クルーズセンター", "short": "中央ふ頭",
             "area": "fukuoka", "lat": 33.610690, "lng": 130.397153,
             "verified": True},
    # ⚠️ 箱崎ふ頭は**まだ乗り場を確認していない**（今の寄港予定は全部 中央５岸）。
    #    座標は入れていない。箱崎に着く便が出たら check() が止めるので、
    #    そのとき社長に乗り場を聞いて入れる（当てずっぽうの座標を配らない）。
    "箱崎": {"name": "箱崎ふ頭", "short": "箱崎ふ頭",
             "area": "fukuoka", "lat": None, "lng": None,
             "verified": False},
}

# ── フェリー太古（野母商船・博多〜五島） ───────────────────────────────
TAIKO_URL = "https://www.nomo.co.jp/taiko/timetable.html"
# 運休日はトップページの「運航カレンダー」（WordPressのBiz Calendar）が持っている。
# 画面には色で出るだけだが、日付の一覧が bizcalOptions に埋め込まれている。
TAIKO_TOP = "https://www.nomo.co.jp/taiko/"
TAIKO_ATTRIBUTION = "出典：野母商船株式会社「フェリー太古 時刻表」"
# 🔴 座標は**博多埠頭タクシー乗り場**（社長が地図上で指定 2026-08-16）。
#    第2ターミナルの建物（OSM 33.605160,130.397642）ではない。客はここへ出てくる。
#    九州郵船（壱岐・対馬）を足すときも同じ乗り場を使う（社長確認済み）。
TAIKO_PORT = {"name": "博多ふ頭第二ターミナル", "short": "博多ふ頭",
              "area": "fukuoka", "lat": 33.604536, "lng": 130.398269}

# ── 九州郵船（壱岐・対馬） ─────────────────────────────────────────────
# 🔴 PDFは読まない。サイトの「ダイヤ検索」を使う（社長が発見 2026-08-16）。
#    検索は**配船パターンA/B/Cを解決済みの時刻**を返すので、カレンダーと
#    突き合わせる必要がない。唐津航路が混ざる心配も無い（航路を指定するため）。
#    2026-08-16 にPDFと突き合わせて完全一致を確認済み。
KYUYOU_SEARCH = "https://www.kyu-you.co.jp/routes/search"
KYUYOU_REFERER = "https://www.kyu-you.co.jp/route/"
KYUYOU_ATTRIBUTION = "出典：九州郵船株式会社「航路・ダイヤ案内」"

# 博多に着く航路だけ。値はサイトのフォームの port_from（実測）。
# 6（壱岐・印通寺）は唐津航路なので叩かない＝博多に来ない。
KYUYOU_FROM = {2: "壱岐（芦辺・郷ノ浦）", 4: "対馬（厳原）", 8: "対馬（比田勝）"}
KYUYOU_TO_HAKATA = 1

# 何日先まで取るか。🔴 むやみに増やさない（1日あたり3回×日数だけ相手を叩く）。
#    ハブは週2回作り直すので、7日あれば次の作り直しまで穴が空かない。
KYUYOU_DAYS = 7
KYUYOU_SLEEP = 2.0  # 問い合わせの間隔（秒）。相手は小さな会社のサイト。

# 🔴 乗り場は太古と同じ（社長確認 2026-08-16）。
#    ジェットフォイルはベイサイドプレイス着だが、客が出てくる乗り場は同じ。
KYUYOU_PORT = dict(name="博多ふ頭第二ターミナル", short="博多ふ頭",
                   area="fukuoka", lat=33.604536, lng=130.398269)

WD = "月火水木金土日"


def fetch(url, timeout=60, headers=None):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def cell_text(c):
    """表のセル1つを文字にする。

    🔴 タグを一律「空白」に置き換えてはいけない（2026-08-16 に踏んだ）。
       福岡市のページは船名を `<a>S</a><a>PECTRUM OF THE SEAS</a>` と
       **2つのリンクに割って**書いている。空白に置き換えると
       「S PECTRUM OF THE SEAS」という存在しない船名になる。
       改行だけ空白にし、それ以外のタグは詰める。
    """
    c = re.sub(r"<br[^>]*>", " ", c, flags=re.I)
    c = re.sub(r"<[^>]+>", "", c)
    return re.sub(r"\s+", " ", html.unescape(c).replace("　", " ")).strip()


def tables(raw):
    """HTMLの表を [[セル,...],...] の形で取り出す（表ごとのリスト）。"""
    out = []
    for t in re.findall(r"<table.*?</table>", raw, re.S | re.I):
        rows = []
        for r in re.findall(r"<tr.*?</tr>", t, re.S | re.I):
            cells = [cell_text(c) for c in
                     re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S | re.I)]
            if cells:
                rows.append(cells)
        out.append(rows)
    return out


# ══════════════════════════════════════════════════════════════════════
# クルーズ客船
# ══════════════════════════════════════════════════════════════════════
def pin_year(rows, today):
    """🔴 表に年が書いていないので、**曜日で年を確定する**。

    「8月18日（火曜）7時00分」の曜日は、年が1つズレれば必ず合わなくなる。
    候補の年を当てはめて全行の曜日が合う年だけを採る。1年に絞れなければ
    やめる（推測で年を決めない＝過去や来年の予定を今日の予定として出さない）。
    """
    cand = []
    for y in (today.year - 1, today.year, today.year + 1):
        ok = ng = 0
        for c in rows:
            m = re.match(r"(\d+)月(\d+)日（(.)曜", c[0])
            if not m:
                continue
            mo, d, w = int(m.group(1)), int(m.group(2)), m.group(3)
            try:
                if WD[dt.date(y, mo, d).weekday()] == w:
                    ok += 1
                else:
                    ng += 1
            except ValueError:
                ng += 1
        if ok and ng == 0:
            cand.append((y, ok))
    if len(cand) != 1:
        raise SystemExit(
            f"✗ 年を確定できません（候補 {[c[0] for c in cand]}）。"
            "表の組み方が変わった可能性があります。人が見てください。")
    return cand[0][0]


def parse_cruise(raw, today):
    tb = tables(raw)
    if not tb:
        raise SystemExit("✗ クルーズの表が見つかりません（ページの作りが変わった）")
    rows = tb[0]
    hdr, data = rows[0], [r for r in rows[1:] if len(r) >= 7]
    want = ["着岸日時", "離岸日時", "船名", "船席", "前港", "次港", "発着"]
    if hdr[:7] != want:
        raise SystemExit(f"✗ クルーズの表の列が変わりました: {hdr}")
    if not data:
        raise SystemExit("✗ クルーズの表が空です")

    year = pin_year(data, today)
    ports = {}
    for c in data:
        m = re.match(r"(\d+)月(\d+)日（.曜）(\d+)時(\d+)分", c[0])
        if not m:
            raise SystemExit(f"✗ 着岸日時が読めません: {c[0]!r}")
        mo, d, h, mi = (int(x) for x in m.groups())
        date = dt.date(year, mo, d)

        # 離岸＝「同日18時45分」「翌日7時00分」「9月4日（金曜）17時00分」
        dep = None
        s = c[1]
        m2 = re.match(r"(同日|翌日)(\d+)時(\d+)分", s)
        m3 = re.match(r"(\d+)月(\d+)日（.曜）(\d+)時(\d+)分", s)
        if m2:
            dd = date + dt.timedelta(days=1 if m2.group(1) == "翌日" else 0)
            dep = {"date": dd.strftime("%Y%m%d"),
                   "h": int(m2.group(2)), "m": int(m2.group(3))}
        elif m3:
            a, b, hh, mm = (int(x) for x in m3.groups())
            dep = {"date": dt.date(year, a, b).strftime("%Y%m%d"),
                   "h": hh, "m": mm}
        # 読めない書き方（未定・「-」等）は出発を空にする。着岸だけでも役に立つ。

        berth = c[3]
        key = "箱崎" if "箱崎" in berth else "中央"
        meta = CRUISE_PORTS[key]
        # 🔴 乗り場が未確認の岸壁に着く便が出たら、当てずっぽうの座標を配らずに止める。
        if not meta["verified"]:
            raise SystemExit(
                f"✗ {meta['name']}に着く便が出ました（{c[0]} {c[2]} / {berth}）。"
                "この港のタクシー乗り場をまだ確認していません。"
                "社長に乗り場を聞いて CRUISE_PORTS に入れてから配ってください。")
        p = ports.setdefault(key, dict(meta, calls=[]))
        p.pop("verified", None)
        p["calls"].append({
            "date": date.strftime("%Y%m%d"),
            "h": h, "m": mi,
            "depart": dep,
            "ship": c[2],
            "berth": berth,
            "from": c[4],
            "to": c[5],
            # 「発着」列＝**その船が出発した国の名前**（社長 2026-08-16 確認）。
            # 🔴 画面には出さない。前港（from）と同じことを言っており、運転手の
            #    判断材料にならない。将来使うかもしれないのでフィードには残す。
            "base": c[6],
        })
    for p in ports.values():
        p["calls"].sort(key=lambda x: (x["date"], x["h"], x["m"]))
    return {
        "key": "cruise_hakata",
        "name": "クルーズ客船",
        "enabled": True,
        "kind": "dated",
        "row_title": "ship",   # 画面の主役＝船名（クルーズは船が主役）
        # 🔴 クルーズだけ「近日の寄港」を出す。2か月で19回しか無いので、
        #    今日の分だけだと画面がほぼ空になり「いつ来るか」が分からない。
        "upcoming": True,
        "source": CRUISE_URL,
        "license": "",
        "attribution": CRUISE_ATTRIBUTION,
        "terms": "https://www.city.fukuoka.lg.jp/sub/tyosakuken.html",
        "ports": list(ports.values()),
    }


# ══════════════════════════════════════════════════════════════════════
# フェリー太古（野母商船）
# ══════════════════════════════════════════════════════════════════════
def parse_taiko_no_service(raw):
    """太古の**運休日**をトップページの運航カレンダーから取る。

    データの在処＝`var bizcalOptions = {...}`（WordPressのBiz Calendar）。
      temp_holidays … 運休日（例 2026-09-28）
      eventdays     … ドック入渠の期間（例 2026-05-10〜05-22・旅客の運航なし）

    🔴 **「運休日」＝その日に博多着が無い日** と読む。根拠はページの但し書き:
       「オレンジ色部分、太古運休日です。**運休日前日23：45発の出航はありません**」
       太古は前夜23:45に博多を出て翌朝8:15に福江着、その日の10:10に福江を出て
       17:50に博多へ戻る。前夜の出航が無い＝翌日の折り返しも無い＝**その日の
       博多着が無い**。日付を1日ずらして読むと、走っている日を運休と出してしまう。
    """
    m = re.search(r"var\s+bizcalOptions\s*=\s*(\{.*?\});", raw, re.S)
    if not m:
        raise SystemExit(
            "✗ 太古の運航カレンダーが見つかりません（ページの作りが変わった）。"
            "運休日が分からないまま配ると、運休の日に『17:50着』と嘘を出します。")
    opt = json.loads(m.group(1))
    days = set()
    for key in ("temp_holidays", "eventdays"):
        for line in (opt.get(key) or "").replace("\r", "\n").split("\n"):
            line = line.strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", line):
                days.add(line.replace("-", ""))
    if not days:
        raise SystemExit("✗ 太古の運休日が0件（読み取りに失敗した疑い）")
    return sorted(days)


def parse_taiko(raw, no_service):
    """上り便（五島発 → 博多着）の博多着時刻を取る。

    表は「港名 / 博多 / 宇久 / 小値賀 / 青方 / 奈留 / 福江」の横並びで、
    2行目が下り便・3行目が上り便。上りの博多は「17:50着」の形。
    """
    for rows in tables(raw):
        if not rows or rows[0][0] != "港名":
            continue
        head = rows[0]
        up = next((r for r in rows[1:] if r and "上り" in r[0]), None)
        if not up:
            continue
        i = head.index("博多")
        m = re.search(r"(\d{1,2}):(\d{2})着", up[i])
        if not m:
            raise SystemExit(f"✗ 太古の博多着が読めません: {up[i]!r}")
        # どこから来たか＝上り便で「発」がある一番遠い港（＝始発）
        origin = ""
        for j in range(len(head) - 1, 0, -1):
            if j < len(up) and "発" in up[j]:
                origin = head[j]
                break
        return {
            "key": "nomo_taiko",
            "name": "フェリー太古（野母商船）",
            "enabled": True,
            "kind": "daily",
            "row_title": "from",   # 画面の主役＝どこから来たか
            "upcoming": False,     # 毎日走るので「近日」を出すと溢れる
            "source": TAIKO_URL,
            "license": "",
            "attribution": TAIKO_ATTRIBUTION,
            "terms": "",
            # 🔴 運休日（法定検査・ドック入渠）は no_service で配る。
            #    ここに残るのは**当日の荒天欠航**だけ＝それは別の仕組みが要る。
            "note": "当日の荒天欠航は反映されません。公式でご確認ください",
            # その日に博多着が無い日（YYYYMMDD）。運航カレンダーから取得。
            "no_service": no_service,
            "ports": [dict(TAIKO_PORT, arrivals=[{
                "h": int(m.group(1)), "m": int(m.group(2)),
                "from": origin, "route": "博多〜五島",
            }])],
        }
    raise SystemExit("✗ 太古の時刻表が見つかりません（ページの作りが変わった）")


# ══════════════════════════════════════════════════════════════════════
# 九州郵船（壱岐・対馬）
# ══════════════════════════════════════════════════════════════════════
def parse_kyuyou(today):
    """ダイヤ検索を日付ごとに引いて、**博多に着く便**だけを集める。

    返るHTMLの形（実測 2026-08-16）:
        <h3>ジェットフォイルヴィーナス/ヴィーナス2</h3>
        <dl class="time-table-block red">
          <dt>122便</dt>
          <dd>壱岐（郷ノ浦） 発 08:45</dd>
          <dd>博多 着 09:55</dd>
        </dl>
    🔴 1つの便が複数の航路の検索結果に出る（224便は壱岐からも対馬からも出る）。
       **(日付, 便名) で重複を取る**こと。
    """
    calls, seen = [], set()
    for i in range(KYUYOU_DAYS):
        d = today + dt.timedelta(days=i)
        for pid, pname in KYUYOU_FROM.items():
            q = urllib.parse.urlencode({
                "month": d.strftime("%Y-%m"), "day": str(d.day),
                "port_from": pid, "port_to": KYUYOU_TO_HAKATA,
            })
            raw = fetch(f"{KYUYOU_SEARCH}?{q}", timeout=30, headers={
                # 🔴 これが無いと 404 が返る（サイトがajaxとして扱うため）。
                "X-Requested-With": "XMLHttpRequest",
                "Referer": KYUYOU_REFERER,
            })
            for head, body in re.findall(r"<h3[^>]*>(.*?)</h3>(.*?)(?=<h3|\Z)",
                                         raw, re.S):
                kind = cell_text(head)
                if not kind:
                    continue
                for dl in re.findall(
                        r'<dl class="time-table-block[^"]*">(.*?)</dl>',
                        body, re.S):
                    m = re.search(r"<dt>(.*?)</dt>", dl, re.S)
                    if not m:
                        continue
                    trip = cell_text(m.group(1))
                    # 区間は <dd> 1つの中に全部入っている（_hakata_leg 参照）。
                    got = _hakata_leg(" ".join(
                        cell_text(x)
                        for x in re.findall(r"<dd>(.*?)</dd>", dl, re.S)))
                    if not got:
                        continue
                    hh, mm, origin = got
                    key = (d.strftime("%Y%m%d"), trip)
                    if key in seen:
                        continue
                    seen.add(key)
                    calls.append({
                        "date": key[0], "h": hh, "m": mm,
                        "ship": f"{trip} {kind}",
                        "berth": "", "from": origin, "to": "", "base": "",
                        "depart": None,
                    })
            time.sleep(KYUYOU_SLEEP)   # 相手を叩きすぎない
    calls.sort(key=lambda c: (c["date"], c["h"], c["m"]))
    return {
        "key": "kyu_you",
        "name": "九州郵船（壱岐・対馬）",
        "enabled": True,
        "kind": "dated",
        "row_title": "from",          # 画面の主役＝「どこから来たか」
        "upcoming": False,            # ほぼ毎日走るので「近日」は出さない
        "source": KYUYOU_REFERER,
        "license": "",
        "attribution": KYUYOU_ATTRIBUTION,
        "terms": "",
        # 🔴 当日の欠航は反映されない（サイト自身がそう明記している）。
        "note": "当日の運航状況は反映されません。欠航は公式でご確認ください",
        "ports": [dict(KYUYOU_PORT, calls=calls, arrivals=[])],
    }


_LEG = re.compile(r"(\S+?)\s+(発|着)\s+(\d{1,2}):(\d{2})")


def _hakata_leg(text):
    """1便ぶんの行程から「博多に着く時刻」と「その直前に出た港」を取る。

    ⚠️ 区間は `<dd>` **1つの中にまとめて**入っている（2026-08-16 実測）。
       `<dd>` ごとに1区間だと思って読むと0件になる。

    text 例: '対馬（厳原） 発 08:50 壱岐（芦辺） 着 11:05
              壱岐（芦辺） 発 11:15 博多 着 13:25'
    → (13, 25, '壱岐（芦辺）')
    """
    legs = _LEG.findall(text)
    for i, (port, kind, h, m) in enumerate(legs):
        if port != "博多" or kind != "着":
            continue
        origin = ""
        for p2, k2, _, _ in reversed(legs[:i]):
            if k2 == "発":
                origin = p2
                break
        return int(h), int(m), origin
    return None


# ══════════════════════════════════════════════════════════════════════
def check(doc, today):
    """🔴 公開前の検査。1つでも赤なら出さない（嘘の時刻を配らない）。"""
    ng = []
    ops = doc["operators"]
    if not ops:
        ng.append("運航会社が0件")

    for op in ops:
        tag = op["key"]
        if not op.get("attribution"):
            ng.append(f"{tag}: 出典が空（表示の条件を満たさない）")
        if not op.get("ports"):
            ng.append(f"{tag}: 港が0件")

        if op["kind"] == "dated":
            calls = [c for p in op["ports"] for c in p["calls"]]
            if not calls:
                ng.append(f"{tag}: 寄港予定が0件")
                continue
            ymd = today.strftime("%Y%m%d")
            future = [c for c in calls if c["date"] >= ymd]
            if not future:
                ng.append(f"{tag}: 今日以降の寄港予定が0件"
                          "（ページが更新されていないか、年の判定が誤り）")
            for c in calls:
                if not (0 <= c["h"] <= 23 and 0 <= c["m"] <= 59):
                    ng.append(f"{tag}: 時刻が異常 {c['date']} {c['h']}:{c['m']}")
                if not c["ship"]:
                    ng.append(f"{tag}: 船名が空 {c['date']}")
                # 🔴 「S PECTRUM OF THE SEAS」型の割れを見張る（cell_text 参照）。
                #    先頭が1文字だけ離れているのは、まずタグの分割ミス。
                if re.match(r"^[A-Za-z] [A-Za-z]", c["ship"]):
                    ng.append(f"{tag}: 船名が割れている疑い {c['ship']!r} "
                              f"({c['date']})")
            print(f"  {tag}: 寄港 {len(calls)}件（うち今日以降 {len(future)}件）"
                  f" / 港 {len(op['ports'])}か所")
            # 🔴 日ごとの本数を必ず出す。黙って減っていても気づけるように。
            per = {}
            for c in calls:
                per[c["date"]] = per.get(c["date"], 0) + 1
            if len(per) > 1:
                print("      日別 " + " ".join(
                    f"{d[4:6]}/{d[6:]}:{n}便" for d, n in sorted(per.items())))
        else:
            arr = [a for p in op["ports"] for a in p["arrivals"]]
            if not arr:
                ng.append(f"{tag}: 到着便が0件")
            for a in arr:
                if not (0 <= a["h"] <= 23 and 0 <= a["m"] <= 59):
                    ng.append(f"{tag}: 時刻が異常 {a['h']}:{a['m']}")
            ns = op.get("no_service") or []
            future_ns = [d for d in ns if d >= today.strftime("%Y%m%d")]
            print(f"  {tag}: 到着 {len(arr)}便 / 港 {len(op['ports'])}か所"
                  f" / 運休日 {len(ns)}日（今日以降 {len(future_ns)}日）")
            if future_ns:
                print("      今後の運休 " + " ".join(
                    f"{d[4:6]}/{d[6:]}" for d in future_ns[:14]))

    if ng:
        print("\n✗ 検査で止めました:", file=sys.stderr)
        for x in ng:
            print("   -", x, file=sys.stderr)
    return ng


def build(check_only=False):
    today = dt.datetime.now(JST).date()
    print("① クルーズ寄港予定を取得中 …", flush=True)
    cruise = parse_cruise(fetch(CRUISE_URL), today)
    print("② フェリー太古の時刻表と運休日を取得中 …", flush=True)
    taiko_ns = parse_taiko_no_service(fetch(TAIKO_TOP))
    taiko = parse_taiko(fetch(TAIKO_URL), taiko_ns)
    print(f"③ 九州郵船のダイヤ検索を取得中 …（{KYUYOU_DAYS}日分×3航路）", flush=True)
    kyuyou = parse_kyuyou(today)

    doc = {
        "version": 1,
        "generated_at": dt.datetime.now(JST).isoformat(timespec="seconds"),
        "operators": [cruise, taiko, kyuyou],
    }

    print("④ 検査")
    if check(doc, today):
        return 1
    if check_only:
        print("\n✓ 検査だけ実行しました（書き出していません）")
        return 0

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"\n✓ 書き出しました: {OUT} ({os.path.getsize(OUT)/1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="作らずに検査だけ")
    sys.exit(build(check_only=ap.parse_args().check))
