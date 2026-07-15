# route_transit_feed

タクシー配車アプリ向けの **時刻表データ基盤（福岡市地下鉄＋JR/西鉄）**。
福岡市交通局の公式Excel（地下鉄）と Yahoo!路線情報（JR/西鉄）から時刻表を
自動取得・パースして静的JSONを生成し、GitHub Pages で無料配信する。
アプリ側はこのJSONを読むだけ。ダイヤ改正時の手動更新＋アプリ再ビルドを不要にする。

- 出力: `out/subway_timetable.json`（地下鉄・公式Excel源）
- 出力: `out/train_timetable.json`（JR/西鉄・Yahoo源・月1・2秒スロットル・差分検知）

`route_event_feed` / `route_poi_feed` と同じ設計思想
（公式源のみ・アプリはスクレイプしない・サーバー集約・秘密情報ゼロ）。

## なぜこの形か

- ダイヤ改正のたびに手動でExcel取得→再ビルド、をやめる。ここを直すだけで全ユーザーに反映。
- **個人情報・APIキーを一切扱わない**（公式時刻表の取得とPages配信のみ）。¥0・サーバー不要。

## 生成物

`out/subway_timetable.json`（アプリ `TrainService` の既存スキーマと一致）
```json
{ "version":1, "generated_at":"...", "source":"...material.php",
  "attribution":"出典: 福岡市交通局（福岡市地下鉄 時刻表）",
  "stations": { "天神": { "lat":.., "lng":..,
    "schedules":[ {"line":"空港線・箱崎線","direction":"福岡空港方面",
      "day_type":"weekday","departures":["05:48","06:00", ...]} ] } } }
```

## 処理

```
pip install -r requirements.txt
python build_subway.py    # 公式Excel2本を取得 → パース → 正規化 → out/subway_timetable.json
```

- 空港線・箱崎線（.xls, xlrd）／七隈線（.xlsx, openpyxl）を公式URLから取得。
- 正規化：七隈線の同名駅の方向分裂（「素の名前」＋「(福岡県)」）を統合／
  駅座標を `subway_station_coords.json`（地下鉄駅は不動＝安定）から付与。
- 取得崩れ時は駅数下限ガードで非0終了＝配信中止（Pagesは前回分を温存）。

GitHub Actions が定期実行し GitHub Pages へ配信（`.github/workflows/build.yml`）。

## データ出典

福岡市交通局（福岡市地下鉄 時刻表・公式資料）。事実情報（駅・方面・発車時刻）のみを配信。
