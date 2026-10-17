# MGP Champs 2026：並行している作業（2026-10-17 時点）

同時に走っている作業と、次の一手の控え。終わったものは消す。仕組みと罠の正本は [mgp-champs-2026.ja.md](mgp-champs-2026.ja.md)。

## 1. 予選の全 Round の処理と公開

- Studio：`round_queue_studio.sh` が `~/mgp/rounds/queue.txt` を上から処理（ログ `queue.out`、Round ごとに `q<N>.loc.log`）。
  今日の分（138〜178）は済み、1 日目（1〜120 のうち録画のある分）を処理中。1 Round 5〜15 分
- Mac：`round_loop.sh`（`build/mgp/round_loop.log`）が仕上げと公開。**この Mac がスリープすると止まる**
  （待ちの sleep が進まない）。`caffeinate -i -w <loop の PID>` で防ぐ。フタを閉じると効かない
- 公開先 https://ghostline.saqoo.sh/mgp-champs-2026/
- 失敗のまま：Round 173〜178（夕方の光で位置合わせが各マス 0〜30%）。直すなら夕方のレースを地図につなぎ足す（Saqoosha の判断待ち）
- 一覧の名前が「Bracket · Race N」から「Qualifying · Race N · Qualifying」に変わった。`round_loop.sh` は両方を受けるよう
  直したが、**動いているループは古い版のまま**（次に止めたときに入れ替える）
- Round 178 の 20:30 の録画（名前は 178）は別物。本物は 18:29（LiveFPV の終了 17:32 ＋ 録画の時計の 1 時間）
- **録画↔Round は名前でなく時刻で決める**（LiveFPV の終了＋1 時間＝録画の時計、録画はその 1〜8 分前に始まる）。名前の付け間違い：
  14:36「50」→ 51、18:30「83」→ 86、10/16 14:44「130」→ 137、16:01「151」→ 152、17:42「165」→ 168、17:54「170」→ 171、
  155 の録画の後半 → 156。仕上げ（`round_mac.sh`）は Studio の `queue.txt` から録画を引く
- 一覧に録画がない Round：29〜36、49、50、121〜136（ほぼ公式ラップ 0）、142 ほか
- 元のファイルが途中までしかない録画（一覧の大きさとも一致）：Round 51（10/15 14:36、4 秒、MINJAE_ の 17.4 秒台の回）、
  10/17 14:33 の C Main「Redemption Race 5」（7.9 秒）

## 2. 最後の 5 ゲート：NEROS のゲートの表と裏のずれ（原因と直し）

- 列（x −131〜−111、奥行き y ≈ 6.6〜7、track 座標）は、背の高いゲート 2 つ（上の枠と脚の間の 2 通りに抜けられる。VelociDrone の
  「上下 2 段」）、その間のバナー、低いゲート 1 つ。順路は全員・毎周、右の上 → 戻る → 左の上 → 右の下 → 左の下 → 端を回る → 低い
- **右の背の高いゲート（NEROS）だけ、手前から見た面と奥から見た面が 0.48 m 離れていた**（柵のない 2 m 以上で比較。左のゲートは
  2〜4 cm、低いゲートは 2 cm）。両側から見えている点は 33,600 中 75 で、束調整に両面をつなぐ手がかりがない。3DGS の
  「写しのゲート」はこれ（Saqoosha の指摘：前後が反対）
- 直し：`LOCAL=1 ZMIN=1.8 ZMAX=4.6 mapfix_row.py mapfinal/sparse/0 mapfix_neros top_frame.json fit01.png.json -118.6 -112.4 5.6 7.9 6.6`
  （柵を巻き込まない高さ、列から 12 m のカメラだけ動かす束調整）→ 差 −0.02 m、3 つのゲートの奥行きが 6.70 / 6.86 / 6.85 m に
- 3DGS の学習し直し：`gs_dataset.py mapfix_neros ... ~/mgp/gsfix` → spirula `train in-the-wild`（Studio の M4 Max で 7 時間 4 分、
  200 万 splat、PSNR 24.1）→ `MGP-Champs-2026-fix.sog`。公開ページの `?scene=MGP-Champs-2026-fix` で比べられる（差し替えは未）。
  見た目の差は小さい：文字が少しくっきりするが、NEROS の上の枠はどちらでもほぼ見えない（FPV は枠を下から抜けるので、
  上から見た姿を学習していない）
- 未：位置合わせのキャッシュ（`loc_cache`）を直した地図で作り直すか。作り直さないと、奥側の pilot の位置は 0.5 m ずれたまま

## 2b. フライスルー（打ち切り）

- 公式フライスルー（youtu.be/XvGI1JULiOQ）は地図に乗らなかった（登録・単独 SfM・区間 SfM・アンカーからの広げ、どれも崩れる）。
  アンカー（手で打った対応点、http://studio:8765/）は、上の原因の確かめ（重ね描き）に使った

- 公式フライスルー（youtu.be/XvGI1JULiOQ）。単独では地図に乗らない（夕方・色加工・同じ印刷のゲート）
- 錨のページ http://studio:8765/（`tools/mgp/anchor_server.py`＋`anchor.html`、保存 `~/mgp/fly15/anchor/anchors.json`）。
  使える錨：8 px 以内 6 組以上・隅 3 つ以上・ぶれ 0.5 m 以内。カメラは錨から較正（`camera.json`、焦点距離 約 460）
- 登録：`tools/mgp/anchor_register.py` を Studio で実行中（`~/mgp/fly15/areg.log`、出力 `~/mgp/fly15/areg/reg`）
- 次：登録できたフレームのつながり（速さ）を確かめる → 地図＋フライスルーで Spirula（Studio、`~/tools/spirula/spirula`）で 3DGS
  → `to_metric.py` → `ply_to_web.py` → `.sog` を差し替え
- 錨の数値と場所の正しさは別物：同じ印刷のゲートなら数値は良くても場所が違う（50 s の 1 回目）。場所は人の目で決める

## 3. 本戦のページ（ライブの音つき）

- 録画：10/16 夜の 13 本（Winner Bracket / Redemption / Final、**4 分割**）は Studio の `raw/` にある。
  10/17 の分は `watch_new.sh 2026-10-17` が拾う（`watch_new_1017.out`）
- 録画は無音（−91 dB）。音は YouTube のライブから取る：A Main `_LDYBkr9M0M`、B `GKc9jo6_rYM`、C `ow1dZGNZYng`、D `GbT8_wnBErs`
  （10/17 時点で配信前）。合わせ方は時計（録画のファイル名の時刻とライブの開始時刻）＋スタートの合図
- 10/16 夜の 13 本は **E Main**（組み合わせ表 `view_heat_sheet&id=10928037`）。今日は D・C・B・A Main
  （表 10932212 / 10932213 / 10932214 / 10932232）。現地時刻で D 9:30〜11:30、C 12:30〜2:30、B 3:30〜5:30、A 7:00〜10:00 PM
- ラップ：`tools/mgp/livefpv_bracket.py <結果ページ> <out.json>`。結果一覧の最初のページは**いちばん新しいイベント**しか出さない
  ので、各 Main はそれが最新のうちに保存する（E Main は `data/mgp/livefpv_e_main.json`、Race 1 はラップが空）。
  組み合わせ表のページは reCAPTCHA つきで読めない
- 録画の名前のレース番号は当てにならない（Race 1 が 2 本、「Race 5-7」）。録画↔レースは時刻で：レースの終了＋1 時間＝録画の時計、
  録画はその数分前に始まる
- **集め続けている（Studio、寝ている間も）**：`watch_new.sh 2026-10-17`（今日の録画。Archive へ移った後も拾う）、
  `finals_collect.sh`（15 分おき：LiveFPV の各 Main を `~/mgp/finals/livefpv_<x>_main.json` に、最初のページの HTML、
  YouTube の配信を終わったものから映像込み 1080p で `~/mgp/finals/stream/<id>.mp4`＋情報 JSON）
- LiveFPV のイベント番号：C Main 521648、D Main 521649、B Main 521650（`?p=view_event&id=`）。E と A は不明
  （E は保存済み、A は最初のページから拾う）
- 配信は途中で切り直されて番号が変わる（D Main だけで 3 本）。だからチャンネルの配信一覧を毎回読む。
  Studio の yt-dlp は 2026.7.4 だと YouTube に 403 で断られた（2026.8.19 に上げて解決）
- 4 分割の切り出しは `LAYOUT=4up extract.py`（720×540 の映像を 480×360 に縮めて、予選と同じ処理に流す）
- **E Main と A Main は夜**（照明の下）。昼の地図に乗るかを E Main の Race 8（`~/mgp/brackets/e08`）で試し中。
  乗らなければ E Main の夜の映像を地図につなぎ足す。急がない（Saqoosha：どうせリアルタイムにはならない）
- やること：録画↔レースの対応、名前付け、ページの束ね方、音

## 4. 済み（この日）

- DVR の列の幅を窓の 25% までに
- 名前付け：2 組入った録画、つながった周、1 周だけの曖昧な一致、古いファイルの消し忘れ
- 空のマスでポーズの変換が止まる問題
