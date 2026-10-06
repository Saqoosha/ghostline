# ghostline

FPV ゴーグルの DVR 映像（HDZero）だけから、全フレームのカメラ姿勢を同じ場所の 3DGS スキャン上に求め、ビューアで
3DGS と DVR を重ねて確かめる。レースなら複数の飛行を 1 つのページで再生する。VDGS（VelociDrone に 3DGS を入れる mod、
https://github.com/Saqoosha/VDGS ）から切り出した。スキャン（`.ply` / `.sog`）と空（`sky.jpg`）を作るのは VDGS 側の道具で、
ここはその出力を読むだけ。

通し・設定・罠の正本は [docs/dvr-localization.ja.md](docs/dvr-localization.ja.md)。ライブ（飛行中）の位置推定は [docs/realtime-tracking.ja.md](docs/realtime-tracking.ja.md)。

## 構成

| 場所 | 中身 |
|---|---|
| `tools/dvr/` | 姿勢推定（CPR 照合 `cpr_track.py` → 束調整 `cpr_ba.py` → 2 周目 `cpr_rematch.py` → `takeoff.py`、評価 `eval_align.py`）、`make_race.py`（race.json） |
| `tools/dvr/rt_*` | ライブの位置推定（`rt_map.py` で他の飛行からキーフレーム地図、`rt_track.py` で過去のフレームだけで追跡・複数人をバッチ・答えを SSE で配信（`PUSH`）・直近の答えの束調整（`BA`））、`rt_trt.py`（LighterGlue と XFeat を TensorRT のエンジンに）、`rt_render.py`（`RENDER=1`：予測姿勢でその場で 3DGS を描いて照合。キーフレームより精度が高い）、`rt_capture.sh`（USB キャプチャ → SRT、録画つき）、`rt_replay.sh`、`ndi_send.py`（録画を NDI で流す試験用）、`rt_power*`（電力の記録）、`rt_linux_setup.sh`（会場用の Linux の環境作り）、`rt_gpu_idle.sh`（合間の GPU を 21 → 7 W に戻す見張り。rt4090 で常駐）、`rt_control.py`＋`rt_control.html`（会場の操作ページ `http://rt4090:8080`：NDI の送り手とマスを選んで tracker を開始 / 停止、状態の表示。rt4090 で常駐）。入力は SRT のほか `ndi://<名前>` で NDI を直接受ける。正本は [docs/realtime-tracking.ja.md](docs/realtime-tracking.ja.md) |
| `viewer/` | Vite + PlayCanvas。`index.html` は 1 飛行のビューア（mark / pad の道具つき、`live` でライブの答えの再生：`src/live.ts` が 100 ms 遅らせた内挿で描く）、`race.html` はレースの再生、`live.html` はライブの表示と録画の再生（`?replay=`） |
| `data/dvr/` | 結果の小さな JSON（姿勢・評価・pad・race.json）。映像と中間物は `build/dvr/`（git の外） |
| `worker/` | https://ghostline.saqoo.sh 。R2（バケット `vdgs`）の `dvr/<name>/` を `/<name>/` として返す |
| `tools/publish-dvr-viewer.sh` | ビューアを組んでページとデータを R2 に上げる。Worker の deploy は要らない |

重い処理（MASt3R・gsplat の描画・束調整）は win4090 の WSL（`~/mastenv`）で回す。向こうの作業フォルダは
`C:\Users\saqoosha\VDGS\dvr` と `VDGS\tools`（歴史的な名前。スクリプトがこのパスを直に書いている）。
同じ機械の 750 EVO に会場用の Ubuntu 24.04（`ssh rt4090`、作業場所 `~/rt`）も入っていて、ライブの tracker はこちらのほうが速く電力も低い。
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
- **録画で電力を測るなら `PACE=1`。** 付けないと次のフレームを待たずに回り、GPU が埋まって本番より高く出る。CPU は torch の OpenMP が空回りして約 100 W 食っていた（`OMP_NUM_THREADS=1` を既定にして 55〜60 W）。数字は realtime 文書の「電池で回す」
- **ライブ入力の比較に Mac の `rt_send.py`（x265＋SRT）を使わない。** 送り手が遅れて、解けるフレームが回ごとに 6,000〜8,600 と揺れた（同じ入力を 4090 機の中や有線の Mac Studio から流すと約 14,000 で揃う）
- **Linux で NDI を受けるには avahi-daemon が要る。** 動いていないと送り手が見つからない。tracker を裏で起動するなら SIGTERM で止める（SIGINT は非対話シェルの裏では無視される。`rt_track.py` は両方受ける）
- **4090 は Linux で CUDA を一度使うと、待機が 7 W でなく 21 W に張り付く。** `/proc/driver/nvidia/suspend` に suspend、resume と続けて書くと戻る（`rt_gpu_idle.sh` が自動で打つ）。tracker の起動前に打っても効かない。`nvidia-drm fbdev=0` は逆効果
- **ライブの速度や精度をいじる前に、設計の報告書を読む**（`docs/realtime-architecture.html`。オフラインの通しは `docs/offline-architecture.html`）。解像度は効かない、1 人 60 fps・4 人で 1 周 9 ms など、測り直しになる数字がそこにある
- **race ページの splat がぼやける**：PlayCanvas の CPU ソートは走行中の依頼を捨てる。カメラが止まったら `resortWhenIdle` がソートを頼み直す
  （PlayCanvas の内部フィールドを読んでいるので、上げたら確かめる）

## 公開

```
bash tools/publish-dvr-viewer.sh jdl-2026-r6 build/dvr/hdz_0067
bash tools/publish-dvr-viewer.sh fdf-2026-r6 viewer/public/flights.json
(cd worker && npx wrangler deploy)     # Worker を変えたときだけ
```

R2 の鍵は 1Password の Environment を `~/.claude/1p-mounts/vdgs.env` にマウントしたもの（VDGS と共用）。
旧 URL `vdgs.saqoo.sh/dvr/<name>/...` は VDGS の Worker がここへ 301 で送る。
