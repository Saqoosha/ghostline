# ghostline

FPV ゴーグルの DVR 映像（HDZero）だけから、全フレームのカメラ姿勢を同じ場所の 3DGS スキャン上に求め、ビューアで
3DGS と DVR を重ねて確かめる。レースなら複数の飛行を 1 つのページで再生する。VDGS（VelociDrone に 3DGS を入れる mod、
https://github.com/Saqoosha/VDGS ）から切り出した。スキャン（`.ply` / `.sog`）と空（`sky.jpg`）を作るのは VDGS 側の道具で、
ここはその出力を読むだけ。

通し・設定・罠の正本は [docs/dvr-localization.ja.md](docs/dvr-localization.ja.md)。

## 構成

| 場所 | 中身 |
|---|---|
| `tools/dvr/` | 姿勢推定（CPR 照合 `cpr_track.py` → 束調整 `cpr_ba.py` → 2 周目 `cpr_rematch.py` → `takeoff.py`、評価 `eval_align.py`）、`make_race.py`（race.json） |
| `viewer/` | Vite + PlayCanvas。`index.html` は 1 飛行のビューア（mark / pad の道具つき）、`race.html` はレースの再生 |
| `data/dvr/` | 結果の小さな JSON（姿勢・評価・pad・race.json）。映像と中間物は `build/dvr/`（git の外） |
| `worker/` | https://ghostline.saqoo.sh 。R2（バケット `vdgs`）の `dvr/<name>/` を `/<name>/` として返す |
| `tools/publish-dvr-viewer.sh` | ビューアを組んでページとデータを R2 に上げる。Worker の deploy は要らない |

重い処理（MASt3R・gsplat の描画・束調整）は win4090 の WSL（`~/mastenv`）で回す。向こうの作業フォルダは
`C:\Users\saqoosha\VDGS\dvr` と `VDGS\tools`（歴史的な名前。スクリプトがこのパスを直に書いている）。

## 踏むと高くつくこと

- **画素比較の loss は両画像を σ 3 px でぼかさないと向きを見ない**（芝が画面の 3 分の 2）
- **位置は画素比較で探さない。** ±6 m の格子探索は正しいアンカーを 6 m 動かして loss を 14% 下げた。偽の極小がどこにでもある
- **数度・1 m を超える外れはまず CPR で直す。** 後に光度 refine を掛けると悪化する
- **人が見るずれは、近い芝の点が作る RANSAC の合意に遠い電柱や木が外れとして捨てられて出る。** 解いた姿勢で描き直す 2 周目の照合で直る
- **束調整の動きの制約は加速度ではなくジャーク**（60 fps では揺れのほうが本物の機動より大きな加速度に見える）
- **gsplat で照合用に描くときは視野外の splat を 1.2 倍で切る**（`tools/dvr/frustum.py`）。切らないと真横の splat が空を灰色にする。
  既定の組み合わせ（d07m）は 1 周目を全部描き（`CULL=0`）、2 周目と評価を DVR に色を合わせたシーンで切って描く
- **他のセッションの `wsl --shutdown` は走っている処理を黙って殺す**
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
