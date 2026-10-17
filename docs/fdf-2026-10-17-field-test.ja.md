# FDF 2026-10-17 の現場試験：その日のコースを作り直して、USB キャプチャでライブ追跡

rt4090 を FDF のフィールドに持ち込み、ゲートと旗を動かした当日のコースで、ライブ追跡を朝から日没まで回した。
その日のうちに、THETA V の 360 動画からシーンを作り直し、前のスキャン（`FDF-2026-R6b-spirula-web-dvr2`）の座標に合わせて、
地図・コースの線・3D のページまでそろえた。ライブ追跡の仕組みの正本は [realtime-tracking.ja.md](realtime-tracking.ja.md)。

## 一日の流れと所要時間

| 段 | 道具 | 時間 |
|---|---|---|
| THETA から取り込み | `tools/theta/theta.swift`（ImageCaptureCore） | 4.4 GB で約 4 分 |
| 左右に切る → 抜き出し＋SAM のマスク | `tools/theta/theta_build.sh` | 4,430 枚、約 30 分 |
| SfM（spirula、`dual-fisheye` のリグ） | 同上 | 23 分、4,430/4,430 登録、0.60 px |
| 学習（`train 360-camera`、300 万 splats、30,000 回） | 同上 | 2 時間 6 分（途中から tracker と GPU を分け合った）、PSNR 26 前後 |
| 前のスキャンに合わせる | `shape_align.py` → `cpr_seeded.py` → `sim_align.py` | 学習と並べて約 40 分 |
| 座標を移す → 地図 | `tools/theta/theta_post.sh` | 地図は GPU が空いていれば 30 秒、分け合うと 3.5 分 |
| 3D 用の `.sog` | `npx @playcanvas/splat-transform@3.4.2 -w in.ply out.sog`（Mac。rt4090 に node は無い） | 1.5 分、43 MB |

撮影は 2 本使った（R0010132：膝の高さ 0.5〜0.8 m、R0010134：約 2.5 m）。16 秒の試し撮りは外した。

## THETA V の動画からシーンを作る

- **THETA V は USB で PTP。Mac のボリュームに出ない。** gphoto2 は macOS の `ptpcamerad` と取り合って
  `Could not claim the USB device` で負ける（kill しても launchd がすぐ起こし直す）。ImageCaptureCore で読む `tools/theta/theta.swift`
  （`swiftc -O theta.swift -o theta; ./theta list; ./theta get <dir> <name> ...`）
- **動画は 1 トラックに魚眼 2 つが横並び（3840×1920）。spirula の 360 の扱い（GoPro `.360`、Insta360 `.insv` のトラック）には乗らない。**
  先に ffmpeg で左右 2 本（1920×1920、NVENC cq 16）に切り、`--skip 8 --keep 0` で同じフレーム番号で抜けばリグの対になる。
  魚眼の円の外は `sam mask --shape "ellipse 0.5,0.5,0.48,0.48"`、人は `--text "person; leg; shoe"`
- **レンズを水平に向けて持つ。** 膝の高さの動画はレンズが上下（空と足元）を向いていて、照合にも地図の視点にも使えなかった
  （`lensroll.py` の正立の回転のばらつき 39°、2.5 m の動画は 3°）
- 学習を短くすると追跡が悪くなる（VDGS の測定、d05 の描く点 p50：7k 回 0.77 m、30k 回 0.54 m）。当日でも 30,000 回まで回す

## 前のスキャンに合わせる（縮尺の無い SfM → 前のスキャンの座標）

THETA に位置の情報は無いので、縮尺・回転・平行移動は前のスキャンから決めた。

1. **見た目の照合は最初は通らなかった。** ゲートと旗が全部動いていて、光も違う（前のスキャンは曇り）。
   tracker の見つけ直し（ドローンの飛行から作った地図）は 0 / 17,420 フレーム、MASt3R の CPR の見つけ直しも 100 inlier に届かなかった
2. **形で合わせる（`shape_align.py`）。** 地面から 4 m 以上の点（木の列・建物・電波塔。ゲートと旗は 2〜3 m なので外れる）を上から見た地図にして、
   縮尺 25〜60（対数で 36 段）× 向き 2° 刻みで、平行移動は FFT の相互相関。一致の度合い 0.795、次点（別の解）0.620。
   **SfM 側を y 上向きにそろえる行列の行列式を確かめる**：最初は −1 で鏡像どうしを合わせていた（0.555）
3. **その初期値から MASt3R で詰める（`cpr_seeded.py`）。** SfM の姿勢を変換して前のスキャンを描き、照合。1 枚あたり数千の inlier、
   答えは予測から 0.5〜1.1 m。69 枚から `sim_align.py`（RANSAC＋Umeyama）で **縮尺 34.29 m/単位、59/69 が inlier、残差 p50 0.22 m**
4. **検算は撮った高さ。** 前のスキャンの地面からのカメラの高さ：膝の高さの動画 0.86 m（言った値 0.5〜0.8）、2.5 m の動画 2.45 m

MASt3R の環境は rt4090 に一時的に入れて（`~/m3env`、tracker の `~/mastenv` とは別）、ディスクのために消した。入れ直しは `tools/dvr/mastenv.sh` の
手順で venv の名前を変える（5 分）。

## ライブの入力：USB キャプチャを rt4090 に直接

- **USB キャプチャ（Guermok、`/dev/video0`）を NDI の送り手にする**：`tools/dvr/usb_ndi.py`（MJPEG 1920×1080 60 fps → NDI `USB`）。
  操作ページ・自動録画・上から見た図が NDI の会場と同じ手順で使える。`sudo systemd-run --unit=usb-ndi --uid=saqoosha --gid=saqoosha -p SupplementaryGroups=video -p Restart=on-failure ... python -u usb_ndi.py /dev/video0 USB`
- MJPEG で読む（YUYV は 1080p で 5 fps）
- 録画の上乗せ：1 時間で約 7 GB（`~/rt/rec/`）。当日 49 本・15 GB

## 3D のページ（`http://rt4090:8080/view/`）

`viewer/live.html` を `VITE_BASE=/view/` でビルドして `~/rt/view/` に置き、操作ページのサーバーが配る（`/view/` は `view/` の中で
いちばん新しい `index.json` のフォルダへ飛ぶ）。データのフォルダは `index.json`（`scene`、`orbit`、`track`）・`scene/<名前>.sog`・`track.json`。

- 届いた流れ（マス）ごとに 1 人。MGP のページ（`mgp.ts`）と同じ描き方：0.12 m の球、不透明・深度ありの軌跡（古い端へ 5 → 1 px、網点で薄れる）、
  黒地に色の塊の名前タグ、白の破線のコースの線（0.12 m、1.0 m／0.7 m）。答えが 1 秒来ないと軌跡が古い端から消えていく
- カメラは Orbit（既定。コースの中心を毎秒 6° で回り、コース全体と飛んでいる人が収まる距離を追う）・Top・Free・各パイロットの Follow。キー 1〜3、4〜
- 名前と国旗：`/api/pilots` が会場のヒート管理（JDL Heat Manager、`http://192.168.3.20:8001/current_pilots` → `[名前 × チャンネル..., ヒート, クラス]`、
  並びはマスの順）を 3 秒おきに読む。国旗は名前から推定（漢字・かな → JP、韓国の姓 → KR）、`<data>/countries.json` で上書き
- `?stream=F1` で 1 人だけ

## コースの線（`tools/dvr/rt_trackline.py`）

ゲートもラップの時刻も無しで、ライブの答えから作る。途切れずに解けた飛行（`GAP` 1 秒、`MIN_LEN` 18 秒以上）の中で、いちばん長い飛行が同じ向きで
何度も通る点で周回に区切り、`make_race.py` と同じ DTW の平均。当日：周回 15、使ったのは 13（中央値 14.8 秒・327 m）、線 323 m。
答えの `.jsonl` は止めるたびに上書きされるので、止めたら `~/rt/live/archive/<日時>/` に退避してから全部をまとめて通す。

## 当日の数字

- 自己テスト（2.5 m の動画を tracker に流す、最終版のシーンと地図）：左右とも 8,710 / 8,710 フレーム、inlier p50 963 / 848
- 実際のヒート（最終版、昼）：1 マスあたり 26〜30 回/秒、遅れ 20〜35 ms。HD 機のレンズは全員 upgrade
- 学習と GPU を分け合うと 7〜12 回/秒、遅れ 50 ms 台まで落ちた
- **夕方は落ちた。** 朝 10:15 のスキャンを 16 時以降に使うと照合の点が減り、解ける回数とレンズの判定が揺れた（R5 が 4 回/秒、レンズが 1〜10 分で行き来）

## 踏んだこと

- **ピンホールの動画を tracker に入れると、OSD のマスクで照合点がほぼ全部捨てられる。** `rt_track.py` と `cpr_track.py` は HDZero の画面の文字を消すマスクを
  カメラ定義の `source_fisheye` から作る。そこに別のカメラ（THETA の 1920 px の魚眼）が入ると全面が「使わない」になり、0 フレームしか解けない。
  tracker 用の定義では `source_fisheye` をピンホールと同じ大きさ（fx・cx・cy を同じ値、k は 0.0 の float）にする。`cpr_seeded.py` は `qvalid[:] = True`
- **rt4090 のユーザーの systemd は ssh が切れると止まる**（linger が切ってある）。`systemd-run --user` の長い処理が `Stopping ...` で消えた。
  `sudo systemd-run --uid=saqoosha --gid=saqoosha --setenv=HOME=...` でシステム側に置く
- **操作ページのサーバー（`ghostline-control`）を再起動すると tracker も止まる**（子プロセス）。tracker を止めてからにする
- **tracker が gsplat の中で 0 除算（SIGFPE、`gsplat_cuda.so`）で落ちた。** 2 回とも F1 のレンズの判定が切り替わった 20〜40 秒後。原因は未確認。
  落ちると答えの `.jsonl` が書かれず、その走りの答えが消える（15:20〜16:46 を失った。映像は録画に残る）
- **uv の環境はキャッシュからのハードリンク。** 環境を消してもディスクは空かず、`uv cache clean` で空いた（7.2 GB）
- 会場の LAN が途中で変わり（rt4090 が 10.1.1.x のルーターの内側へ）、ヒート管理に届かなくなった。ページは Tailscale の名前 `http://rt4090:8080/` で開く

## rt4090 に残っているもの

- 作業場 `/mnt/data/fdf-2026-10-17/`（`raw/` の動画、`scene/`、`align/`（`fine68.json` が当日の変換、`shape.json` が形の合わせ）、`logs/`）。`/mnt/data` は fstab に無いので、起動後は `sudo mount -t ntfs3 /dev/nvme1n1p2 /mnt/data`
- シーン `~/scenes/FDF-2026-10-17-s30000.ply`、地図 `~/rt/map_fdf-2026-10-17-s30000.npz`、コースの線 `~/rt/tracks/fdf-2026-10-17.json`、3D `~/rt/view/fdf-2026-10-17/`
- 答えの退避 `~/rt/live/archive/20261017-{1503,1517,1717}/`（それぞれ 14:18〜15:03、15:10〜15:17、16:48〜17:17 の走り）、録画 `~/rt/rec/`

## 残っていること

- gsplat の SIGFPE をレンズの切り替えの録画で再現する。落ちても答えが残るように、`.jsonl` を走りながら書く
- 地上と夕方の画でレンズの判定が揺れる（少ない点数どうしの比較で決めている）
- 時間ごとの解けた割合（朝のスキャンが夕方どれだけ落ちるか）を、退避した答えと録画の作り直しでまとめる
- 録画の置き場所を `/mnt/data` にして fstab に入れる
- JDL：前の晩に撮って一晩で回す。RTK（`~/repos/Personal/rtk-clapper`、iPhone の動画にチャープ音で GNSS 時刻を合わせる）付きの歩きと 360 の歩きを 1 つの SfM にまとめれば、合わせの段が要らない
