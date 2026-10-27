# ghostline

FPV ゴーグルの DVR 映像（HDZero）だけから、全フレームのカメラ姿勢を同じ場所の 3DGS スキャン上に求め、ビューアで
3DGS と DVR を重ねて確かめる。レースなら複数の飛行を 1 つのページで再生する。VDGS（VelociDrone に 3DGS を入れる mod、
https://github.com/Saqoosha/VDGS ）から切り出した。スキャン（`.ply` / `.sog`）と空（`sky.jpg`）を作るのは VDGS 側の道具で、
ここはその出力を読むだけ。

通し・設定・罠の正本は [docs/dvr-localization.ja.md](docs/dvr-localization.ja.md)。ライブ（飛行中）の位置推定は [docs/realtime-tracking.ja.md](docs/realtime-tracking.ja.md)。大会のあとの見返しの作り直し（夜の作り直し・配信で埋める・その日の DVR から SfM で合わせ直す）は [docs/night-rebuild.ja.md](docs/night-rebuild.ja.md)。電力と電池は [docs/rt-power.ja.md](docs/rt-power.ja.md)。その日のコースを 360 動画から作り直して会場で回した通しは [docs/fdf-2026-10-17-field-test.ja.md](docs/fdf-2026-10-17-field-test.ja.md)。iPhone の AR（スキャンと飛行を部屋に重ねる）は [docs/ar-iphone.ja.md](docs/ar-iphone.ja.md)。

## 構成

| 場所 | 中身 |
|---|---|
| `tools/dvr/` | 姿勢推定（CPR 照合 `cpr_track.py` → 束調整 `cpr_ba.py` → 2 周目 `cpr_rematch.py` → `takeoff.py`、評価 `eval_align.py`）、`make_race.py`（race.json） |
| `tools/dvr/night_*`・`daysfm/` | 大会のあとの見返し：`night_heats.py`（DVR の切り出しと race.json）、`night_put.py`（rt4090 から Worker の口で直接上げる）、`mgp_fuse.py` と `daysfm/`（その日の DVR の SfM の地図で外れた選手を合わせ直し、ライブの答えと組み合わせて選ぶ。MGP の `sfm.py`・`localize.py` は rt4090 の `~/mgp`）。正本は [docs/night-rebuild.ja.md](docs/night-rebuild.ja.md) |
| `tools/dvr/rt_*` | ライブの位置推定（`rt_map.py` で他の飛行からキーフレーム地図、`rt_track.py` で過去のフレームだけで追跡・複数人をバッチ・答えを SSE で配信（`PUSH`）・直近の答えの束調整（`BA`））、`rt_trt.py`（LighterGlue と XFeat を TensorRT のエンジンに）、`rt_render.py`（`RENDER=1`：予測姿勢でその場で 3DGS を描いて照合。キーフレームより精度が高い）、`rt_capture.sh`（USB キャプチャ → SRT、録画つき）、`rt_replay.sh`、`ndi_send.py`（録画を NDI で流す試験用）、`rt_power*`（電力の記録）、`rt_day_gate.py` → `rt_day_power.py`（大会の配信の録画から 1 日ぶんの電力量を見積もる）、`rt_linux_setup.sh`（会場用の Linux の環境作り）、`rt_gpu_idle.sh`（合間の GPU を 21 → 7 W に戻す見張り。rt4090 で常駐）、`rt_rec.py`（NDI の送り手を全部、どれかのマスに絵があるあいだ録画。一覧は操作ページの `/rec`、判定は `rt_signal.py` を tracker と共有）、`rt_control.py`＋`rt_control.html`（会場の操作ページ `http://rt4090:8080`：NDI の送り手とマスを選んで tracker を開始 / 停止、状態・届いた映像・上から見た軌跡の表示、レンズは「自動」で tracker が見分ける。rt4090 で常駐。真上からの絵は `rt_topview.py`、`/obs` は図だけのページ（OBS のブラウザソース用）、描く部分は `rt_topview.js` を両ページで共有）。`/view/` は 3D のページ（`viewer/live.html` のビルド、`~/rt/view/`）、`/api/pilots` は会場のヒート管理からマスごとの名前。入力は SRT のほか `ndi://<名前>` で NDI を直接受ける。`usb_ndi.py` は rt4090 に挿した USB キャプチャを NDI の送り手にする。`rt_ndiview.py` は 3D のページをヘッドレスの Chrome で描いて NDI（「ghostline 3D」）・SRT（`:9000`）の片方か両方で運営のスイッチャーへ出す（出力・大きさ・fps とカメラは操作ページの「配信」、`ghostline-ndiview.service`）。`rt_trackline.py` はライブの答えだけからコースの線（周回を DTW で平均）。`rt_replay_day.sh` は 1 日の録画を tracker に実時間で流し直す（`PUSH=8765` なら操作ページの 3D のページにそのまま出る。画面録画もこれで撮った）。比べは `rt_replay_compare.py`（解けた割合）・`rt_replay_accuracy.py`（コースの線からの距離）・`rt_replay_verify.py`（真値なしの検算：答えの姿勢でスキャンを描き、マスの絵と照合し直して、その姿勢で説明できる点を数える）。`rt_map_grow.py` はその日の実フレームを地図に足す（操作ページが描いて照合のときヒートの合間に自動、「今すぐ育てる」も。tracker は `RKF=1`・`MAPWATCH` で止まらず差し替え）。正本は [docs/realtime-tracking.ja.md](docs/realtime-tracking.ja.md) |
| `tools/mgp/` | スキャン無しの通し：MultiGP の公開 8up 録画から SfM の地図（`extract.py` → `sfm.py` / `chain.sh`）、3DGS、全フレームの位置合わせ（`localize.py`）、ラップで pilot 名（`name_slots.py`）、race.json（`make_mgp_race.py`）。正本は [docs/mgp-champs-2026.ja.md](docs/mgp-champs-2026.ja.md) |
| `viewer/` | Vite + PlayCanvas。`index.html` は 1 飛行のビューア（mark / pad の道具つき、`live` でライブの答えの再生：`src/live.ts` が 100 ms 遅らせた内挿で描く）、`race.html` はレースの再生、`mgp.html` は MultiGP の全 Round の再生（`rounds.json`、右に DVR の列）、`live.html` はライブの表示と録画の再生（`?replay=`）：マスごとに 1 人、MGP のページと同じ球・軌跡・名前タグ・コースの線・カメラ（Orbit / Top / Free / Follow）。観客向けは `event.html`（大会の 1 日の入口、R2 では `index.html`）・`watch.html`（ライブ、中継の WebSocket、DELAY で配信に合わせる）・`heat.html`（ヒートの見返し：`heats/<id>/race.json` があれば DVR 付き、無ければ `live.json`）、共通部品は `src/aud/`（`bun test src/aud`） |
| `ios/GhostlineAR/` | iPhone の AR アプリ（XcodeGen）。ARKit の 1 フレームを `tools/dvr/rt_vps.py`（rt4090 の位置合わせサーバー、`ghostline-vps`）に送って部屋に合わせ、スキャンの点・飛行の軌跡・DVR を重ねる。真上の 3DGS の絵と、ドローンの目（DVR、3DGS を魚眼に曲げて着地）へ飛んで切り替え。正本は [docs/ar-iphone.ja.md](docs/ar-iphone.ja.md) |
| `tools/theta/` | THETA V の 360 動画からシーンを作り、縮尺の無い SfM を前のスキャンに合わせ（形の一致 → MASt3R → Umeyama）、地図まで（rt4090）。手順は field-test 文書 |
| `data/dvr/` | 結果の小さな JSON（姿勢・評価・pad・race.json）。映像と中間物は `build/dvr/`（git の外） |
| `worker/` | https://ghostline.saqoo.sh 。R2（バケット `vdgs`）の `dvr/<name>/` を `/<name>/` として返す。`/<大会>/api/*` は観客向けの中継（Durable Object `LiveRoom` が大会の 1 日に 1 つ、`rt_relay.py` からの書き込みは secret `RELAY_KEY`）。試験は `worker/test/smoke.sh`、ページごとは `worker/test/local-page.sh` |
| `tools/publish-dvr-viewer.sh` | ビューアを組んでページとデータを R2 に上げる。Worker の deploy は要らない |

重い処理（MASt3R・gsplat の描画・束調整）は win4090 の WSL（`~/mastenv`）で回す。向こうの作業フォルダは
`C:\Users\saqoosha\VDGS\dvr` と `VDGS\tools`（歴史的な名前。スクリプトがこのパスを直に書いている）。
同じ機械の WD SN850X 2TB（NVMe、2026-10-19 に 750 EVO から移した）に会場用の Ubuntu 24.04（`ssh rt4090`、作業場所 `~/rt`）も入っていて、ライブの tracker はこちらのほうが速く電力も低い。
起動は rEFInd の画面で 5 秒待つと Linux、矢印キーで Windows（Windows から Linux に入るのも rEFInd で選ぶ）。画面と電源は JetKVM（app.jetkvm.com、ATX の Reset あり）。

## 踏むと高くつくこと

- **画素比較の loss は両画像を σ 3 px でぼかさないと向きを見ない**（芝が画面の 3 分の 2）
- **位置は画素比較で探さない。** ±6 m の格子探索は正しいアンカーを 6 m 動かして loss を 14% 下げた。偽の極小がどこにでもある
- **数度・1 m を超える外れはまず CPR で直す。** 後に光度 refine を掛けると悪化する
- **人が見るずれは、近い芝の点が作る RANSAC の合意に遠い電柱や木が外れとして捨てられて出る。** 解いた姿勢で描き直す 2 周目の照合で直る
- **束調整の動きの制約は加速度ではなくジャーク**（60 fps では揺れのほうが本物の機動より大きな加速度に見える）
- **gsplat で照合用に描くときは視野外の splat を 1.2 倍で切る**（`tools/dvr/frustum.py`）。切らないと真横の splat が空を灰色にする。
  既定の組み合わせ（d07m）は 1 周目を全部描き（`CULL=0`）、2 周目と評価を DVR に色を合わせたシーンで切って描く
- **他のセッションの `wsl --shutdown` は走っている処理を黙って殺す**
- **ライブの照合と XFeat は TensorRT のエンジン（`GLUE=` / `XFEAT=`、`tools/dvr/rt_trt.py`）、PnP は PoseLib（既定）で回す**（4 人で 1 周 28 → 9 ms、精度は同じか良い）。精度を取るなら `RENDER=1 RCLIP=2 SCENE=<地図の .ply>` で予測姿勢でその場で描いて照合する（d05 の答え p50 0.19 → 0.11 m、新しいコースはスキャンのカメラだけの地図で足りる。4 人では遅延が約 20 → 35 ms）。
  PyTorch の LighterGlue に autocast の fp16（`mp`）を掛けない（解けるフレームが半分になる。TensorRT の fp16 は問題ない）。照合は 1 組ずつでなくバッチで呼ぶ
- **tracker の速さは、走行中の実際の入力で測る。** PnP を合成データ（400 点）で 3 ms と見積もっていたが、走行中は約 900 点で MAGSAC が 11 ms かかり、いちばんの詰まりだった
- **ライブの送り手（GStreamer）の出口は `sync=false`。** 既定だと Mac ではフレームが塊で届き、最新の 1 枚しか見ない tracker が 22 Hz に落ちる（直すと 51 Hz）
- **`rt_track.py` の numpy は BLAS 1 スレッド。** 外すと 32 コアの機械で小さな行列にスレッドが立ち、束調整つきの tracker が 54 → 33 Hz に落ちる
- **録画で電力を測るなら `PACE=1`。** 付けないと次のフレームを待たずに回り、GPU が埋まって本番より高く出る。CPU は torch の OpenMP が空回りして約 100 W 食っていた（`OMP_NUM_THREADS=1` を既定にして 55〜60 W）。数字は [docs/rt-power.ja.md](docs/rt-power.ja.md)
- **ライブ入力の比較に Mac の `rt_send.py`（x265＋SRT）を使わない。** 送り手が遅れて、解けるフレームが回ごとに 6,000〜8,600 と揺れた（同じ入力を 4090 機の中や有線の Mac Studio から流すと約 14,000 で揃う）。Mac Studio の `ndi_send.py` は `nohup` で切り離すと映像が届かない（ssh につないだまま裏で回す）。同じ名前の送り手を 2 つ同時に動かさない
- **Linux で NDI を受けるには avahi-daemon が要る。** 動いていないと送り手が見つからない。tracker を裏で起動するなら SIGTERM で止める（SIGINT は非対話シェルの裏では無視される。`rt_track.py` は両方受ける）
- **4090 は Linux で CUDA を一度使うと、待機が 7 W でなく 21 W に張り付く。** `/proc/driver/nvidia/suspend` に suspend、resume と続けて書くと戻る（`rt_gpu_idle.sh` が自動で打つ）。tracker の起動前に打っても効かない。`nvidia-drm fbdev=0` は逆効果
- **HDZero Nano 90 はレンズが 2 種類で、カメラの定義も 2 つ**（`data/dvr/cams/`）。合わないほうを当てると一致点が 3 分の 1 になり、軌跡が 1〜2 m ぶれる。ライブで 1 人だけ線が乱れたら、tracker より先にレンズを疑う（`rt_camsweep.sh`）。「自動」の見分けは、実際にレンズを切り替えて、そのレンズ自身に追わせてから採点する（1 回約 2 秒、うち約 1 秒は軌跡が乱れる）。1 回の試しは飛んでいる場所で点が決まるので、直近 8 回の平均で決め、ヒート管理の名前があれば選手ごとにその日のうち覚える（10/25 の流し直しで解けた割合 71 → 76.5%。1 回で決めていた版は 4 マスに 1〜2 マスが逆のレンズに乗っていた）。切り替えずに予測姿勢を借りて他方を採点する形は、間違ったレンズに乗っている時に決められない（試して捨てた。数字は realtime 文書）。操作ページのログに `lens stock (0 / 105 inliers)` のように片方が 0 の決定が出たら、見失いの最中に採点した疑い
- **上から見た図（`rt_topview.js`）の点の時刻は、届いた時刻でなくフレーム番号（`i / fps`）で置く。** 届いた時刻だと処理の 30〜40 ms の揺れで点の間隔が乱れ、軌跡がカクつく（ページは 60 fps で軽いのに、動きだけが不揃いに見える）。操作ページと OBS ページ（`/obs`）は描く部分とスライダーの設定を共有する
- **ライブの速度や精度をいじる前に、設計の報告書を読む**（`docs/realtime-architecture.html`。オフラインの通しは `docs/offline-architecture.html`）。解像度は効かない、1 人 60 fps・4 人で 1 周 9 ms など、測り直しになる数字がそこにある
- **cyndilib の NDI の Receiver は使い回す。** 捨てて作り直すと、ガベージコレクションの中の `NDIlib_framesync_destroy` が GIL を握ったまま止まり、操作ページのサーバーごと固まった（`rt_rec.py`）
- **Python から ghostline.saqoo.sh に送るときは user-agent を名乗る。** urllib の既定の名前は Cloudflare が 403（error 1010）で弾き、会場の中継の書き込みが届かなかった（`rt_relay.py` は `ghostline-relay/1`）
- **ピンホールの動画を tracker や `cpr_track.py` に入れるときは、カメラ定義の `source_fisheye` を同じ大きさにする。** 両方とも HDZero の OSD を消すマスクをそこから作り、別のカメラの魚眼（THETA の 1920 px）が入るとマスクが全面「使わない」になって 0 フレームしか解けない
- **rt4090 で長い処理は `sudo systemd-run --uid=saqoosha ...` で回す。** ユーザーの systemd（`--user`）は ssh が切れると止まる（linger が切ってある）。`ghostline-control` を再起動すると子の tracker も止まる
- **tracker が落ちると、その走りの答え（`live/<マス>.jsonl`）は書かれない。** 止めたら `live/archive/` に退避する。2026-10-17 に gsplat の中の 0 除算（SIGFPE、`gsplat_cuda.so`）で 2 回落ちた原因は、**視野で切った splat が 0 個のまま gsplat 1.5.3 に渡ったこと**（渡す数が 0 だと毎回落ちる。描いた結果が 0 個になるのは平気）。dmesg の `trap divide error` の位置が、0 個を渡す試験と同じ命令（`gsplat_cuda.so`+0xa955e）。`frustum.rasterize_culled` と `rt_render.py` の RBATCH の経路は、0 個なら不透明度 0 の splat を 1 つ渡して空の絵を返す
- **地図の絵をその日の光に合わせる。** 晴れの朝のスキャンだけの地図は、雨の午後に解ける割合 65%（その日の実フレームを足して 90%）。スキャンはレースと同じ時間帯・天気で撮り、30,000 回まで学習する。実フレームを足すなら直前のヒートの分（時間帯の離れたフレームは逆効果）。10/25 は tracker が GPU を 22 GB まで持って育てを止め、決勝の雨に対応できなかった。原因は地図の大きさではなく、差し替えるたびに古い地図が GPU に残ったこと（直した。1.28 万枚の地図で 6.5 GB のまま）。手順と数字は realtime 文書の「地図とスキャンを当日の光に合わせる」
- **その日の DVR の SfM の地図にヒートを足すとき、MGP の `register.py` をそのまま使わない。** データベースの全部の画像で地図を組むので、一晩で 30 ヒートの画像が入ると 1 本 45 分を超え、語彙の木の照合は土台に 0 枚しか乗せなかった。`tools/dvr/daysfm/register3.py`（組む画像を土台と新しいヒートに絞り、土台と直接照合）で 1 本 2〜7 分。シェルの `while read` の中で ffmpeg を呼ぶと、ffmpeg が標準入力の残りの行を食べる（`-nostdin` か `read -u 3`）。正本は [docs/night-rebuild.ja.md](docs/night-rebuild.ja.md)
- **tracker の答えの `done` は、同じ送り手のマスなら同じ時間軸**（送り手の最初のフレームが基準）。2026-10-18 までは各マスが自分の最初の絵を基準にしていて、同じフレームでも R4 と R5 で 1.265 秒、F1 と R2 で 38.6 秒（SRT の流し直しでは最大 154 秒）ずれていた。それ以前の `live/archive/` と `replay/` の答えをマスどうしで並べるときは、同じ `i` の `done − lat/1000`（`lat` はミリ秒）の差を引いてそろえる（同じ `i` が無いマスは、`done − lat/1000` を `i` に直線で当てはめて比べる）。`lat` はずれていないので、会場の 3D のページと観客向けの送り手は影響を受けていない
- **録画を SRT で流し直すと、tracker のフレーム番号は録画より 120 フレーム（2 秒）遅れて始まる**（2026-10-17 の夕方の 8 本、どの走りも 120〜121）。受け始めに録画の頭が落ちる。流し直しの答えを録画の映像や DVR に合わせるときは足す。`rt_replay_verify.py` が走りごとに測る（`SHIFT`）
- **会場で録画を流し直すとき `PUSH=8765` を付けると、観客向けの中継がそれをその日のヒートとして公開する**（中継は操作ページの中で 8765 を読む）。2026-10-24 の夜に 3 本載った。中継の大会を `staging-*` に向けるか、`PUSH` を付けない（空の `PUSH=` は tracker が落ちる）
- **1 日回した tracker は、操作ページの「停止」で止めない。** 300 秒で SIGKILL され、書き終わっていないマスの答えが消える（6.7 時間の走りは 4 マスで 14 分半かかった）。前の `live/<マス>.jsonl` を退避してから、tracker の PID に直接 SIGTERM を送って待つ。数字は realtime 文書の「大会の当日の運用」
- **NDI の送り手に音声が乗ると、新しく作った受け手が NDI SDK 6.1.1 の中で落ちる**（`ndir:audio` の segfault）。操作ページの中にあった録画の見張りは落ちずに固まり、JDL R7 10/25 は 11:49〜13:03 を録画しなかった。いまの録画は操作ページの子プロセス（`rt_rec.py`）で、落ちても、つながったままフレームが 30 秒来なくても（`REC_STALL`）2 秒後に立ち上がり直し、`/rec` に赤字で出る。ただし音声が乗っている間は、新しい受け手が毎回落ちるので録れない。まず送り手（ZowieBox）の音声を切る。PGM は音声つきなので録画できない
- **中継の 4 分割のマスは下 12 行が黒い**（640×360 のうち絵は 348 行）。オフラインの通しに入れるときは `crop=640:348:0:0` してから 960×720 に戻す
- **rt4090 のヘッドレス Chrome は `--use-angle=gl-egl` でないと GPU を使わない。** 既定は SwiftShader（CPU）で 2 fps、Vulkan は GPU プロセスが落ちる。配信を止めるのに `Page.setWebLifecycleState` の frozen を使わない（戻してもページが hidden のまま描かない。ページの rAF を `ghostlineHold` で止める）。`rt_ndiview.py` の SRT の ffmpeg に `-fflags nobuffer` を付けると mjpeg のパイプの入力がほとんど通らず、絵が黒くなる（数字と OBS の設定は realtime 文書）
- **同じ印刷のゲートが並ぶコースを何レースもまとめて SfM すると壊れる。** きれいな 1 レースの地図に `register.py` で 1 レースずつ足す。
  地図で 1 周に 1 回 60 m 飛ぶのはゲートの取り違え（`mapfix.py`）。3DGS に見える薄い「写し」は本物のことがある（点の観測元で確かめる）
- **隣り合う同じ絵柄（MGP の最後の列の幕 2 枚）は、地図に列の 2 組目を 1 枚ぶんずらして作る。** 濃く見えるほうがずれた組で、
  薄い「ゴースト」が本物だった。片側の点を消して合わせ直しても画像は元の組に戻るだけで見分けられない。幕より上（ゲートの上段）の
  点の観測で画像を分けて直す（`tools/mgp/mapfix_unfold.py`、経緯は `docs/mgp-champs-2026-threads.ja.md` の 2）
- **別の時刻の映像は地図に乗りにくい**（同レース 98% → 3 時間半後 52〜62%）。レンズではなく影。時間帯の違うレースを地図に足す
- **夜の映像の SfM は、誤差が小さくても位置が潰れていることがある。** GLOMAP は 1.15 px・向きも合うのに、カメラが 1 点のまわりに
  星形に散った。魚眼の歪みを 4 つ自由にすると束調整も止まる。incremental と SIMPLE_RADIAL_FISHEYE で組み、地図ができるたびに
  外れ値の検査（`night_model_check.py`）と真上の図（`night_model_plot.py`）で確かめる。手順は `docs/mgp-champs-2026.ja.md` の「夕方と夜の決勝」
- **MASt3R の `fast_reciprocal_NNs` は遅い。** 8,192 行ずつの小さな塊で GPU に何千回も命令を出し、1 組 0.16〜0.27 秒（推論は 0.08 秒）。
  GPU を複数の処理で分けると 1.4〜2.1 秒に膨らむ。`tools/mgp/cpr_gap_lib.reciprocal_nns` は同じ組を 0.07〜0.12 秒で出す
- **描いて照合した 1 コマずつの姿勢は、SfM の姿勢よりぶれる**（MGP の夜の決勝で中点からのずれ 0.67 m 対 0.27 m）。位置の確かめに使い、
  答えには SfM の姿勢を残す（`tools/mgp/night_keep.py`）
- **`tools/mgp/publish_mgp.sh` に Round を並べると、上がる `rounds.json` がその Round だけになる**（ページの一覧から他が消える）。
  差し替えは Round を並べずに全部で流す（変わったファイルだけが送られる）
- **MGP のページで、DVR の動画を `<video>` に直接取らせると、同じ HTTP/3 の接続のほかの取得が止まる。** `<video>` は十分ためると
  `bytes=0-` の取得を開いたまま読むのをやめ、読まれないデータが接続ごとの流量制御の枠を占める。スキャン（`.sog`）が毎回 13〜15 MB
  （全体 23 MB）で止まって見張りの取り直しを待ち、昼 → 夜の Round の切り替えでは次の `race.json` が 12 秒届かなかった。curl では
  再現しない。動画と音は `fetch` で最後まで読んで blob で渡し、スキャンが決まってから始め、Round を移ったら打ち切る
  （`viewer/src/mgp.ts` の `loadMedia`・`whenScanSettled`）
- **8up 録画の帯の名前は映像のマスとずれることがある。** マス→pilot はラップの並びで決める（`name_slots.py`）
- **race ページの splat がぼやける**：PlayCanvas の CPU ソートは走行中の依頼を捨てる。カメラが止まったら `resortWhenIdle` がソートを頼み直す
  （PlayCanvas の内部フィールドを読んでいるので、上げたら確かめる）

## 公開

```
bash tools/publish-dvr-viewer.sh jdl-2026-r6 build/dvr/hdz_0067
bash tools/publish-dvr-viewer.sh fdf-2026-r6 viewer/public/flights.json
bash tools/publish-event.sh jdl-2026-10-25 <シーンのフォルダ> "JDL 2026 R7 DAY 1"   # 観客向け（--track / --night もある、DRY_RUN=1）
bash tools/publish-site.sh             # ghostline.saqoo.sh/ のランディングページ（site/index.html、一覧は手書き）
(cd worker && npx wrangler deploy)     # Worker を変えたときだけ
```

R2 の鍵は 1Password の Environment を `~/.claude/1p-mounts/vdgs.env` にマウントしたもの（VDGS と共用）。
旧 URL `vdgs.saqoo.sh/dvr/<name>/...` は VDGS の Worker がここへ 301 で送る。
