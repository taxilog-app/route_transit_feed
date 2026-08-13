#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""道具そのものが壊れていないかを、**取得を始める前に**調べる。

【なぜ要るか（2026-08-13 の実害）】
build_train.py が `jst_now_iso()` を呼んでいるのに、**定義だけが抜け落ちて**いた
（2026-08-01 の修正で消えた）。結果:

  ・70駅すべての取得に成功 → その直後の書き出しで NameError → 配信中止
  ・配信されないので**公開中のデータは7/31版のまま**
  ・アプリは動き続け、画面にも異常が出ない
  ・**2週間、誰も気づかなかった**（フェリーを足そうとして偶然発見）

しかも取得に27分かけた後で落ちるので、**毎回27分を捨てていた**。
Yahoo!へ70駅ぶん問い合わせた後で落ちる＝相手にも無駄な負荷をかけていた。

【この道具がすること】
各スクリプトを「実行せずに読み込む」だけ。名前の未定義・import の失敗は
ここで全部出る。数秒で終わるので、取得の前に必ず通す。

使い方:
    python3 check_scripts.py
"""
import ast
import builtins
import glob
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__"}


def undefined_names(path):
    """そのファイルの中で「呼んでいるのに定義が無い」名前を返す。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    defined = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            defined.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                defined.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.arg):
            defined.add(n.arg)
        elif isinstance(n, ast.comprehension) and isinstance(n.target, ast.Name):
            defined.add(n.target.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            defined.add(n.name)
    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return sorted(used - defined - BUILTINS)


def loads_ok(path):
    """実行せずに読み込めるか（import の失敗・トップレベルの誤りを拾う）。"""
    spec = importlib.util.spec_from_file_location("_chk", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)   # __main__ ではないので main() は走らない
        return None
    except SystemExit:
        return None                    # 引数不足で抜けるのは正常
    except Exception as e:             # noqa: BLE001
        return f"{type(e).__name__}: {e}"


def main():
    ng = 0
    files = sorted(f for f in glob.glob(os.path.join(HERE, "*.py"))
                   if os.path.basename(f) != os.path.basename(__file__))
    print(f"道具の点検（{len(files)}ファイル）")
    for f in files:
        name = os.path.basename(f)
        miss = undefined_names(f)
        err = loads_ok(f)
        if miss:
            ng += 1
            print(f"  🔴 {name}: 定義が無いのに呼んでいる → {miss}")
        elif err:
            ng += 1
            print(f"  🔴 {name}: 読み込めない → {err}")
        else:
            print(f"  ✅ {name}")
    if ng:
        print(f"\n🔴 {ng}件が壊れている。**取得を始めないこと**"
              f"（27分かけてから落ちる）")
        return 1
    print("\n🟢 道具は健全。取得へ進んで大丈夫です")
    return 0


if __name__ == "__main__":
    sys.exit(main())
