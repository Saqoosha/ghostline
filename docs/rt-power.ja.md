# ライブの tracker の消費電力と電池運用

ライブの位置推定（[realtime-tracking.ja.md](realtime-tracking.ja.md)）を会場の電源・電池で回すときの測定。

## 電池で回す（消費電力、2026-10-05）

会場に 4090 機を持ち込み、ポータブル電源（EcoFlow DELTA 2 Max、2,048 Wh）で回す場合の数字。GPU は `nvidia-smi`（200 ms ごと）、CPU はパッケージの
RAPL（Windows の `\Energy Meter(rapl_package0_pkg)\Power`、1 秒ごと）で測った。マザーボード・メモリ・ファン・電源の損失は入っていない。
録画は `PACE=1` で実時間に歩かせる。付けないと次のフレームを待たずに回し、GPU が常に埋まって電力が本番より高く出る。
測る道具は `tools/dvr/rt_power.ps1`（Windows 側で記録しつつ `rt_power_run.sh` を呼ぶ）と `rt_power_an.py`（追跡中の平均を出す）。

| 4 人同時、`RENDER=1 RCLIP=2` | GPU | CPU | 計 | d05 処理 / 遅延 p50 | レース 3 本の遅延 p50 |
|---|---|---|---|---|---|
| これまで | 188 W | 94 W | 282 W | 45 Hz / 33 ms | 37〜38 ms |
| **OpenMP 1 スレッド（いまの既定）** | 191 | 62 | 253 | 46 / 32 | 35〜37 |
| **それに GPU の電力上限 150 W** | 147 | 61 | **208** | 42 / 35 | 38〜40 |
| 上限 100 W | 100 | 78 | 177 | 29 / 46 | 58〜65（30 → 22 Hz） |
| クロック固定 2100 MHz（上限は 450） | 123 | 87 | 210 | 38 / 38 | 42〜47 |

d05 1 本（`RENDER=1`）は GPU 120 W・CPU 104 → 54 W。無負荷は GPU 22 W・CPU 31 W。答えの精度はどの行も変わらない（d05 p50 0.11〜0.12 m）。

- **CPU の大半は torch の OpenMP の空回りだった。** 16 本のスレッドが各 33% 回り、CPU は負荷に関係なく約 100 W（1 本のほうが 4 本より多い）。
  `OMP_NUM_THREADS=1` で消え、速さも精度も同じ。`rt_track.py` の冒頭で既定にした。`OPENCV_FOR_THREADS_NUM=1` は効かない
- **GPU は電力上限 150 W が手ごろ。** 200 W 以上ではほぼ下がらない（もともと平均 188 W）。100 W ではクロックが 1,200 MHz まで落ちて遅れる。
  クロック固定は同じ電力でも上限より遅い（2100 MHz で 1 周 p50 16 → 25 ms）。上限は `nvidia-smi -pl 150`（管理者、再起動で 450 に戻る）
- **WSL の CUDA は待つ間ずっとコアを回す。** primary context に BLOCKING_SYNC を立てても変わらない（`.item()` の繰り返しで CPU 時間 1.08 s / 壁 1.08 s）。
  event を 0.2 ms ごとに覗いて寝かせると CPU 時間は 1/4.5 になるが、tracker に入れると CPU −4 W・遅延 +2〜3 ms で割に合わなかった。
  残る CPU 約 60 W（無負荷 +30 W）は、このメインスレッドの待ちと WSL の ioctl（CUDA の命令が Windows に渡る分）。py-spy で見るとメインスレッドは追跡中ずっと動いている。CPU のターボ切り（電源プランの最大 99%）は差なし
- **受信機が絵を出していないマスは処理しない（`SIGNAL=1`、ライブ入力のとき）。** マスが単色（受信機の「受けていない」画面）か砂嵐なら、そのフレームを tracker に渡さない。
  全マスがそうなら、ループは待つだけになる。
  - 判定：Event VRX の 2×2 の録画（アナログ、707 秒）では、灰・青・黒の単色の画面は画素のばらつきが 0、絵は 8 以上。
    砂嵐は縦に隣り合う行の相関で分かれる（砂嵐 0.1〜0.3、絵 0.8〜0.9。0.5〜0.7 は弱い電波で絵がかすかに残る）。境目は 0.5。1 マス 0.09 ms
  - この録画では、絵があったのはマスの合計の 47% の時間、4 マスとも空は 13%。取りこぼした飛行は約 0.1 秒
  - ライブの経路で通した（Mac の `rt_send.py` → SRT → WSL の tracker、3 マス）。全マスが空のあいだ GPU は 150 W 前後から 22 W に落ち、絵が戻ると次のフレームから処理する
  - 映像が来ていても飛んでいるとは限らない（電源を入れたまま地面に置いた機体）。画が動いていないマスも止めると 47 → 42〜45% にしかならず、離陸の瞬間を取りこぼすので入れていない
  - **HDZero（FDF の準決勝の 2×2、463 秒）でも同じ判定が効く。** 空のマスは灰（128）か黒の単色。相関が低いのはデジタルの崩れ（色の縦縞が 0.1〜0.2、ブロックノイズまみれが 0.4〜0.5）で、
    どちらも位置を解けない。3 つのヒートのあいだに全マス空が約 75 秒ずつ 2 回あり、全マス空は録画の 35%、絵のあるマスは平均 4 つのうち 1.35
  - その録画をライブの経路で流して比べた（4 マス、`RENDER=1`、GPU＋CPU パッケージ）：

    | | 平均 | ヒート中 | ヒートのあいだ | 解けたフレーム（4 マス計） |
    |---|---|---|---|---|
    | `SIGNAL` なし | 251 W | 250 W | 251 W | 8,872 |
    | `SIGNAL=1` | **129 W** | 167 W | 60 W | 8,614 |

    なしだと空の灰色のマスでも探し直しを続けて GPU をふさぎ、4 マスとも 20 Hz に落ちていた。ヒートのあいだの 60 W は何もしていないとき（53 W）とほぼ同じ
- **Windows で直に回すと遅すぎる。** 同じ版（torch 2.11 cu128、gsplat 1.5.3、TensorRT 11.3、エンジンは `eng_win/` に作り直し）を Windows の venv で回した。
  CUDA を寝て待たせる `CUDA_SYNC=block` は Windows では効く（`.item()` の繰り返しで CPU 時間 1.02 → 0.00 s）。けど各段が遅い（4 人で描画 7.6 → 13.3 ms）。

  | 4 人、`RENDER=1` | GPU＋CPU | 1 周 p50 | d05 処理 / 遅延 p50 | レース 3 本の処理 / 遅延 p50 |
  |---|---|---|---|---|
  | WSL | 191＋62 W | 15.5 ms | 46 Hz / 32 ms | 30 Hz / 35〜37 ms |
  | Windows で直に | 160＋50 W | 32.3 ms | 31 Hz / 47 ms | 25 Hz / 57〜60 ms |
  | Windows で直に、`CUDA_SYNC=block` | 180＋46 W | 25.1 ms | 39 Hz / 37 ms | 29 Hz / 42〜44 ms |

  電力が下がって見えるのは、主に処理するフレームが減ったから。gsplat は Windows ではそのままではビルドできない（torch のヘッダの引数名 `small` が Windows のマクロとぶつかり、`-Wno-attributes` を MSVC が受けない）。
- **Linux で直に動かすと、電力が下がって速くなる（2026-10-06）。** 4090 機の 750 EVO（SATA）の 143 GB に Ubuntu 24.04.5 を入れた（ホスト名 `rt4090`、LAN の DHCP）。
  2026-10-19 に容量が尽きて（97%）、WD SN850X 2TB（`nvme2n1`：p1 が ESP、p2 が root）へ rsync で丸ごと移した。ESP も rEFInd ごと写し、ファームウェアの起動順の先頭は `rEFInd (SN850X)`。
  移したあと、750 EVO の古い ESP（sda5）と root（sda6）は区画ごと消し、それを指す起動のエントリも消した（750 EVO に残るのは Windows 側の区画と、インストーラの区画 `UBUNTU24`）。作業データの `/mnt/data` も同じ root の ext4 に移した（もとは D: の NTFS。D: は Ubuntu からはもう使わない）。署名済みの rEFInd と shim をそのまま写したので、MOK の登録も署名もやり直さずにセキュアブートのまま起動した。
  起動は rEFInd（Linux の ESP に入れた起動の選択画面）：5 秒待って SN850X の Ubuntu、矢印キーで Windows も選べる。設定は `/boot/efi/EFI/refind/refind.conf` の最後の段。
  セキュアブートは有効のまま（切ると BitLocker が回復キーを求めうる）なので、rEFInd は shim の上に載せ、この機械の鍵（`/etc/refind.d/keys/refind_local.*`）で署名し、MOK に登録した。
  Ubuntu 24.04 の rEFInd 0.13.2 は SBAT の区画を持たず、shim 15.8 に `Verification failed: (0x1A) Security Violation` で拒まれる。本家の 0.14.2（SBAT あり）を `sbsign` で署名して
  `EFI/refind/grubx64.efi` と `drivers_x64/ext4_x64.efi` を置き換え、`apt-mark hold refind` で古い版に戻されないようにした。BIOS の F8 の起動メニューも使える
  入り口は `ssh rt4090`（Tailscale、起動するだけでつながる。Windows の Tailscale はログインしないと立たない）。作業場所は `~/rt`、エンジンは `eng_linux/`、Windows の C: は `/mnt/c` に読み取り専用。
  環境は `tools/dvr/rt_linux_setup.sh`、電力は `tools/dvr/rt_power_linux.sh`（RAPL を `/sys/class/powercap` から読む）。
  ドライバ 595-server-open、gsplat 1.5.3 は手直しなしでビルドできる（最初の描画でビルドが走り、ninja と nvcc が PATH に要る）。
  Ubuntu Pro（無料の個人枠）につないで ESM Apps / Infra を有効にした。ffmpeg・GStreamer・libsrt など universe の包みのセキュリティ修正は ESM からしか来ない。
  自動更新は止めてあるので、会場の前に手で `sudo apt update && sudo apt full-upgrade`。26.04 には上げない（CUDA 12.9 と新しい GCC で gsplat の再ビルドが通るか、
  NVIDIA の倉庫とドライバが揃うかが未確認で、24.04 は ESM で 2034 年まで保守される）

  | | GPU＋CPU | 1 周 p50 | 遅延 p50 |
  |---|---|---|---|
  | 何もしていない：WSL（Windows） / Linux | 22＋31 W / 20＋9 W | | |
  | 4 人、`RENDER=1`：WSL / Linux | 253 W / **243 W** | 15.5 / **11.5 ms** | 32〜37 / **27〜32 ms** |
  | 同じく GPU 上限 150 W：WSL / Linux | 208 W / **195 W** | 16.6 / 12.1 ms | 35〜40 / 29〜34 ms |
  | 1 人：WSL / Linux | 176 W / **149 W** | 10.4 / 9.1 ms | 14.4 / 12.6 ms |
  | 準決勝をライブで、`SIGNAL=1`：WSL / Linux / Linux＋150 W | 129 W / **105 W** / 99 W | | |

  答えの精度はどの行も同じ（d05 p50 0.11〜0.12 m）。
  上の「準決勝をライブで」は Mac の `rt_send.py`（x265 でエンコードして SRT）から送った値で、送り手が毎回 6,000〜9,000 フレーム遅れ、解けたフレームが回ごとに 6,153〜8,614 と揺れた。
  会場では 4090 機が NDI を直接受けるので、それに近い形として 4090 機の中から録画をそのまま流して（`ffmpeg -re -c copy` → localhost の SRT）比べ直した：

  | 準決勝、4090 機の中から、`SIGNAL=1` | GPU＋CPU | ヒート中 / あいだ | 解けたフレーム（4 マス計） | 1 周 p50 |
  |---|---|---|---|---|
  | WSL（1 回） | 137 W | 170 / 75 W | 13,868 | 11.9 ms |
  | Linux（2 回） | **125 W** | | 14,091 / 13,930 | 11.3 ms |

  Mac から送ったときの倍近く解け、回ごとの差は 1% 程度。WSL は GPU が低く（80 対 92 W）CPU が高い（57 対 33 W）。

- **会場の入力は NDI を直接受ける（`ndi://<名前の一部>`、2026-10-06）。** 会場では 4090 機を持ち込み、Event VRX の NDI（1080p の 2×2）を同じ LAN で受ける。
  受け口は `cyndilib`（pip、`libndi` を同梱するので NDI SDK は要らない）の FrameSync を 2 ms ごとに覗く形で、NDI のタイムスタンプが変わったら新しいフレーム。
  `receive()` で 1 枚ずつ取る形はライブラリが全部受けているのに 3 分の 1 を取りこぼした。Linux は avahi-daemon が動いていないと送り手が見つからない。
  `GRID=1920x1080` でマスは 960×540（`0:0:960:540` など）。送り手の試験用は `tools/dvr/ndi_send.py`（録画を 1080p 30 fps の NDI で流す）。
  **会場ではブラウザから操作する：`http://rt4090:8080`**（`tools/dvr/rt_control.py`＋`rt_control.html`、`ghostline-control.service` で常駐、`rt_linux_setup.sh` が入れる）。
  見えている NDI の送り手と地図（全マス共通）を選び、マスごとに使うか・名前を決めて開始 / 停止する（画面の大きさと fps は開始のとき送り手から読む）。動いているあいだはマスごとの信号・処理した枚数と位置が出た回数（毎秒）・一致点（inlier）、GPU の電力と状態（P8 を「待機」、それ以外を「動作中」と出す）、CPU パッケージの電力（RAPL の `energy_uj` を毎秒読む。root だけが読めるファイル（CVE-2020-8694 の対策）なので、service の `ExecStartPre` で操作ページのユーザーのグループにだけ開ける（`chgrp`＋`0440`。失敗しても起動はして、ページのラベルに理由が出る）。turbostat の PkgWatt と 2 W 以内で合う）、映像が届いてから位置が出るまでの時間（直近 1 秒の中央値。tracker の「ms each」は合間の待ちを含むので使わない）、ログが 1 秒ごとに出る。電力・温度・処理時間・マスごとの位置が出た回数は直近 5 分の線も出る（履歴はサーバーが持つので、後から開いても見える）。
  置き場所は rt4090 の `~/rt`（`tools/dvr/` の中身を `scp`）。`rt_control.py` を変えたら `sudo systemctl restart ghostline-control`（tracker ごと止まる）、ページの HTML と `rt_topview.js` は毎回ディスクから読むので再読み込みだけ。起動するのは `SIGNAL=1 PUSH=8765`＋TensorRT のエンジン、チェックで `RENDER=1 RCLIP=2 SCENE=…` と屋外の束調整。出力は `~/rt/live/<名前>.jsonl` など、前回の設定は `~/.ghostline-control.json`。ログインは無い（会場の LAN / Tailscale の中で使う）ので、追加の設定に書けるのは tracker の変数だけ（`rt_control.py` の `KNOBS`）。届いた映像は各マスの後ろに薄く出る（送り手の低帯域の映像を 1 秒に 2 枚、ページが開いているあいだだけ受ける。輪で濃さを切り替え）。上から見た図は、シーンを真上から平行投影で 1 回描いた絵（`rt_topview.py`、2.6 秒、`live/topview-*.jpg` に残る。範囲は地図のキーフレームの位置の 99% ＋ 4 m）に、各マスの直近 12 秒の軌跡と名前を重ねる。ページの主役はこの図で、軌跡はブラウザが tracker の `PUSH`（8765）を直接読んで描く（届かなければ 1 秒ごとの状態で描く）。点の時刻は届いた時刻ではなくフレーム番号（`i / fps`）で、届いた時刻の最も早いものに合わせる（処理の 30〜40 ms の揺れで点の間隔が乱れ、軌跡がカクついた）。図の右下の「なめらかさ」と「遅らせる」で軌跡の描き方を決める（既定 80 ms・250 ms、`~/.ghostline-view.json`、動作中でも変えられ、OBS のページにも効く）。なめらかさは前後の答えを時間でガウス平均する σ、遅らせると先頭を前後の答えの間で内挿して毎フレーム描く。準決勝の答え 2.6 万件で、1 フレームごとの揺れ p90 は σ 0 / 50 / 80〜100 / 150 ms で 0.75 / 0.13 / 0.09 / 0.07 m、答えからずれる量 p50 は 0 / 0.08 / 0.2 / 0.46 m。100 ms を超えると角を削るだけ。遅れは 2.5σ＋約 40 ms 要る（短いと平均に先の答えが入らず先頭が引きずられる）。250 ms で内挿できない途切れは 0.2%。**OBS に出すなら `http://rt4090:8080/obs`**：この図だけのページ。ブラウザソースを比率を保ったまま埋め、はみ出す端は切る。線と名前は 1080p 向けに太く大きい。`?trail=秒`（既定 12）、`?course=1` でキーフレームの点も。設定の「コースの線」に `race.json`（`make_race.py` の `track`、`~/rt/tracks/`）を選ぶと、コースの線（25 cm おきの点を Catmull-Rom の曲線で、薄い点線）とスタート / ゴールのゲートが両ページに重なり、図の範囲は大きさを変えずにコースの線の中心へ動く。描く部分は `rt_topview.js` を 2 つのページで共有。サーバーの負荷は 1 コアの約 4%、tracker の処理 1 回は 11.4 ms のまま。

  **観客向けページへの送信**（`rt_relay.py`、ghostline.saqoo.sh/<大会>/）：`scp tools/dvr/rt_relay.py tools/dvr/rt_control.* rt4090:~/rt/`、
  `sudo systemctl edit ghostline-control` で `[Service]` に `Environment=RELAY_BASE=https://ghostline.saqoo.sh RELAY_EVENT=<大会> PILOTS_URL=<ヒート管理の current_pilots>`、鍵は `~/.config/ghostline/key`（600、Worker の secret `RELAY_KEY` と同じ値）。
  再起動は tracker が止まっているときだけ（子の tracker も止まる）。操作ページの上の帯に「送信中」「未送信 N 秒」が出る。ヒートの答えは `live/relay/<日付>/<ヒート>.json`、区切りは `live/relay/heats-<日付>.jsonl`。
  答えの時刻は rt4090 の壁の時計のまま中継に渡る。会場の箱の時計がずれると、見る人の遅れと中継の backlog（80 秒）も同じだけずれる（2026-10-18 は timesyncd で +3.3 ms）。
  夜の作り直し（`night_heats.py`）は流し直しのフレーム番号に 120 フレーム（`--shift`）を足して録画に合わせる。

  **3D のページを運営のスイッチャーへ配信する**（`rt_ndiview.py`、`ghostline-ndiview.service`、`rt_linux_setup.sh` が入れる、2026-10-23）：
  ヘッドレスの Chrome が `/view/?ndi=1`（パネルと DVR の枠なし）を 4090 で描き、screencast の JPEG を NDI（名前「ghostline 3D」）か SRT（`srt://rt4090:9000`、NVENC の H.264、MPEG-TS、待ち受けは 1 台）で出す。
  操作ページの「配信」で出力（NDI / SRT / 両方 / 止める）・大きさ（720p / 1080p）・fps（30 / 60）を選ぶ（`~/.ghostline-out.json`。変わると送り手が終わり、service が 1 秒後に新しい設定で立ち上げ直す。止めている間は Chrome を起こさない）。
  「配信のカメラ」で全体・真上・マスの追いかけとズーム（`~/.ghostline-cam.json`）。ページは `/api/cam/events`（SSE）で押した瞬間に受ける。押してから SRT の絵が変わるまで 0.55 秒（カメラは 0.7 秒かけて滑る）。fps を下げるとページ自身がその間隔でしか描かない（`?fps=`）ので、GPU も splat の並べ替えも JPEG も減る。
  配る：`scp tools/dvr/rt_ndiview.py tools/dvr/rt_control.* tools/dvr/rt_topview.js tools/dvr/rt_rec.py rt4090:~/rt/`、ページは Mac で `(cd viewer && VITE_BASE=/view/ npx vite build --outDir /tmp/view --emptyOutDir)` して `scp -r /tmp/view/{assets,live.html} rt4090:~/rt/view/`（`rt_linux_setup.sh` はビルドを配らない）。
  録画係（`rt_rec.py`）は自分の配信を録らない（`REC_SKIP`、既定 `(ghostline 3D)`。外すと 16 Mbps で録り続ける）。
  Chrome は `--use-angle=gl-egl` でないと GPU を使わない（既定は SwiftShader で 2 fps、Vulkan は GPU プロセスが落ちる）。X は要らない。

  | 配信の形（spirula の学習と同時で揺れる値） | CPU | CPU パッケージの増え |
  |---|---|---|
  | NDI 1080p 60 fps | 約 5 コア（Chrome 3、Python 1〜2：JPEG の展開と NDI の SpeedHQ 圧縮） | +50〜110 W |
  | SRT 720p 30 fps | 約 1.4 コア（大半は Chrome のページ、送り手は 0.03） | +20〜35 W |

  運営の OBS の受け方は `http://rt4090:8080/obs-setup`（操作ページの「OBS」。NDI・SRT・ブラウザソースの設定を、開いたときの会場の IP で書く）。
  **受け手がいるときだけ描く**：NDI の受け手（`get_num_connections`）も SRT の相手（ffmpeg の「Output #0」の行。相手がつないだときに初めて出る）も無い状態が 5 秒続くと、ページの rAF を止め（`window.ghostlineHold`）、screencast も止める。最後の 1 枚は 0.5 秒ごとに送り続ける。来れば約 1 秒で描き始める。止めている間は GPU 10〜11 W・CPU 約 10 W で、「止める」と同じ。描いている間は `/tmp/ghostline-ndiview.rendering` を毎秒更新し、`rt_gpu_idle.sh` はこれが 5 秒以内なら打たない（描画中の Chrome は P8・約 20 W で、見張りが打つと効かずに倍々で打ち続け、そのうちの 1 回で描画が 10 秒止まって送り手が立ち上げ直した）。`Page.setWebLifecycleState` の frozen は使わない（active に戻してもページが hidden のままで、二度と描かない）。
  JDL 2026 R7（2026-10-24）の軽いシーン（80 万 splat SH1）で描いているときの増え（待機 GPU 11 W・CPU 10 W から）：SRT 720p30 は GPU +8・CPU +11 W、NDI 720p30 は +9・+21 W、NDI 1080p60 は +26・+65 W。
  NDI|HX（H.264 の NDI）は NDI Advanced SDK（商用ライセンス）でしか送れないので使っていない。
  **OBS のメディアソースで SRT を受けると、既定の調べ込み（5 MB、6 Mbps で約 7 秒ぶん）をそのまま抱えて 6〜10 秒遅れる。** 「FFmpeg Options」に `fflags=nobuffer analyzeduration=1000000 probesize=1000000`、Network Buffering 0 MB、Reconnect Delay 1〜2 秒、ハードウェアデコードあり で 3〜4 秒。ここから先は OBS の中の溜めで、送り手の側は 0.55 秒。遅れを詰めたいなら OBS の NDI ソースか、ブラウザソース（`http://rt4090:8080/view/?ndi=1&fps=30`、描くのは運営の PC）。
  送り手の ffmpeg に `-fflags nobuffer` を付けない（mjpeg をパイプで受ける入力ではほとんど何も通さず、OBS が黒くなった）。調べ込みは `-probesize 32 -analyzeduration 0` で消す（既定の 5 MB は 720p 30 fps の JPEG で約 4 秒ぶんを抱える）。

  | 準決勝、1080p NDI、4 マス、`SIGNAL=1`、Linux | 解けたフレーム | GPU＋CPU 平均 / ヒート中 / あいだ |
  |---|---|---|
  | 4090 機の中から送る | 13,956 | 136.5 / 180 / 55 W |
  | **Mac Studio から有線の LAN で送る**（2 回） | **14,081 / 14,181** | **122.8 / 163 / 46 W**、122.2 / 163 / 45 W |
  | 同じく GPU の電力上限 150 W | 14,247 | 119.5 / 159 / 45 W |

  届いてから答えまではマスごとに p50 15〜27 ms、p90 21〜32 ms（ログの「arrival -> pose」。「latency ms (frame arrives -> pose)」の行は受けた枚数で時刻を数えるので、NDI では当てにならない）。
  **この形では電力上限 150 W は 3 W しか効かない。** ヒート中の GPU は平均 122 W で、上限が削るのは山だけ（4 人が飛び続ける試験は平均 190 W だったので効いた）。
  ヒートのあいだは、最後のフレームから約 20 秒は GPU が P2 のまま 56 W、そのあと P8 に落ちる。**Linux の 4090 は CUDA を一度使うと P8 が 7 W でなく 21 W に張り付き**、プロセスが終わっても戻らない
  （`torch.zeros(1, device='cuda')` を 1 回で再現。何もしなければ起動から 1 時間 43 分後も 7 W。ドライバ 595.91.07 の open 版）。tracker は合間もコンテキストを持つので、放っておくと会場の合間は 21 W。
  **21 W のときにドライバの suspend と resume を続けて書くと 7 W に戻る**（`echo suspend | sudo tee /proc/driver/nvidia/suspend && echo resume | sudo tee /proc/driver/nvidia/suspend`）。4 人ぶんの tracker（5 GB）が NDI を待つ合間に打つと 20.9 → 6.9 W、約 2 秒（プロセスが無ければ 0.8 秒）で、tracker は解き続け、次の 2 回のヒートの合間も 8 W だった（21 W から打った 4 回とも下がった）。
  効くのは打ったときに生きていたプロセスのあいだだけ：**tracker の起動前に打っても意味がなく**（読み込んだ時点で 21 W）、次に起動したプロセスはまた 21 W に戻る。
  **自動で打つのが `tools/dvr/rt_gpu_idle.sh`**（`ghostline-gpu-idle.service`、root で常駐、`rt_linux_setup.sh` が入れる）：P8 で 15 W 以上が 20 秒続いたら 1 回打つ（ヒート中は P2 なので発火しない。下がらなければ間隔を 60 秒から倍に）。記録は `journalctl -u ghostline-gpu-idle`。起動直後の CUDA 1 回で 20.6 → 6.9 W に自分で戻し、続く tracker は読み込み後も 3 回のヒートの合間も 7.2〜8.1 W。
  **平均への効果は小さい。** 準決勝の通し（NDI、Mac Studio から）で 122.2 → 120.7 W、解けたフレーム 14,181 → 14,108。全マス空は約 74 秒が 2 回（録画の 32%）で、どの合間も最初の 20 秒は P2 の 56 W。1 回目の合間は発火まで 21 W・打った直後の約 10 秒が 54 W で、30.7 → 34.1 W とかえって増える。発火済みの 2 回目は 30.6 → 21.1 W。tracker をヒートの前から起動しておけば全部の合間が 2 回目の形になり、この録画で平均約 −3 W。合間が長い本番ほど効く（壁の電力では未確認）。CUDA を使っても 10〜12 W のまま上がらない起動が 1 回あった（理由は未確認、発火しない）。合間の代わりに tracker を SIGSTOP で止めて測らない（GPU が P2 の 56 W に張り付く）。
  効かなかった：persistence mode、`nvidia_uvm` の取り外し、`nvidia-smi -pl`、コンソールの消灯、測定の頻度、`--gpu-reset`（primary GPU なので拒否）、`options nvidia-drm fbdev=0`（コンソールが simpledrm に移り起動直後から 21 W。逆効果）。同じ症状の報告は NVIDIA のフォーラムの [3090・525 以降](https://forums.developer.nvidia.com/t/idle-power-usage-problem-p8-after-debian-driver-distupgrade-470-525-rtx-3090/257061)、[headless](https://forums.developer.nvidia.com/t/high-idle-power-consumption-in-headless-server-without-monitor-connected/311064)（suspend / resume の回避策はここ）、[570 で増えた](https://forums.developer.nvidia.com/t/increased-idle-consumption-with-driver-570/321460)

  受けるだけなら 1080p 30 fps を 1 枚も落とさず、送ってから届くまで 31.5 ms、1 枚の取り出し 0.6 ms
- **NDI の送り手は全部、どれかのマスに絵があるあいだ録画する**（`tools/dvr/rt_rec.py`、2026-10-07）。操作ページのサーバーが tracker と関係なく常に動かし、一覧は `http://rt4090:8080/rec`（再生・保存・削除。録画中のものは消せない。操作ページの「録画」から。録画中は赤い点と本数）。
  見えた送り手ごとに低帯域の映像（約 640 幅）を 1 秒 5 回見て、tracker と同じ判定（`tools/dvr/rt_signal.py`、`SIGNAL=1` と共有）でどれかのマスに絵が出たら、フル解像度も受けて ffmpeg（既定 `h264_nvenc`、送り手の fps のまま、16 Mbps）で `~/rt/rec/<日時>_<送り手>-<名前のハッシュ 6 桁>.mp4` に書く。4 マスとも空が 5 秒続くか送り手が消えたら切る。
  ファイルは断片化した mp4 のまま（電源が落ちてもそこまで再生できる）。**会場では詰め直さない**（索引を先頭に移す remux はファイルをもう一度まるごとコピーする）。ブラウザで途中へ飛べないときは、あとで `ffmpeg -i x.mp4 -c copy -movflags +faststart y.mp4` で詰め直す。横に `.json`（送り手、開始時刻、マスごとに絵があった割合）。絵が見えてからフル解像度がつながるまでの約 0.5 秒は入らない。変数は `REC`（0 で止める）・`REC_DIR`・`REC_FPS`（0 で送り手のまま。Event VRX は 60）・`REC_RATE`（16M）・`REC_HOLD`・`REC_ENC`。
  **準決勝の全編（Mac Studio から NDI、rt4090、NVENC）で 3 本に分かれた**：131 / 125 / 58 秒、ヒートのあいだの全マス空で切れた。ここまでの数字は 30 fps・12 Mbps のときのもの。**いまの既定は送り手の fps（Event VRX は 60）と 16 Mbps**（1 時間で約 7 GB、rt4090 の空き 88 GB で約 12 時間）：届いた画を 1 枚ずつ書き、送り手が止まったら直前の画を足して時間を合わせる。60 fps で送って 60/1・37.1 秒で 2,225 枚を確認。色の変換（BGRX → yuv420p）は NVENC に任せる：60 fps 1080p で ffmpeg の CPU が 1 コアの 63% → 11%、出力は同じ yuv420p・Main。**60 fps の上乗せは約 12 W**（送り手も 60 fps、tracker は 30 に間引く、同じ準決勝・設定で 1 回ずつ）：GPU＋CPU 平均 123.9 → 136.0 W、うち CPU 35.5 → 45.2 W（フル解像度の NDI をもう 1 本 60 fps で受けて復号し、毎秒約 500 MB を ffmpeg に渡す分）、GPU 88.4 → 90.8 W。ヒート中は 168.7 → 187.2 W。ファイルは 60/1・約 15 Mbps（3 本、135 / 126 / 62 秒）。解けたフレームは録画あり 12,763・なし 13,721 で、差はほぼ SAQ のマス（915 対 2,134。録画なしの 30 fps の回でも 929 が出ていて、録画とは関係しない）。録画だけを回すと GPU が待機 7.7 W（P8）→ 56 W（P2、NVENC が P2 に上げる）、CPU パッケージ 10 → 15 W（ffmpeg が 1 コアの 77%、録画係が 60%）、切ると P8 に戻る。**tracker と並べると上乗せは約 4 W**（同じ準決勝、4 マス、`RENDER=1`＋束調整：tracker だけ GPU＋CPU 125.3 W、録画あり 129.2 W。ヒート中は tracker が GPU をどのみち P2 にしている）。解けたフレームは録画あり 14,021。**rt4090 は NVENC の部品を別に入れる**（ヘッドレスのドライバには `libnvidia-encode` が無く、`h264_nvenc` が開けない。`rt_linux_setup.sh` が入れる）
  **cyndilib の Receiver を捨てて作り直さない。** 捨てた Receiver をガベージコレクションが片付けると、`NDIlib_framesync_destroy` が GIL を握ったまま返らず、サーバーの全スレッドが止まった（macOS、TERM も効かない）。録画係は送り手ごとに 2 つ、操作ページの背景の画（`Preview`）は 1 つ作って、`set_source` / `disconnect` で使い回す
- **会場用に Linux を絞った。** 時計は日本時間で持つ（`timedatectl set-local-rtc 1`。UTC のままだと Linux で起動したあと Windows が 9 時間遅れる）。
  自動更新・snap・cloud-init・使わない記憶装置の仕組みなど 47 個を止めた（`rt_linux_setup.sh` の最後の段、消さずに止めるだけ）。何もしていないとき GPU＋CPU 30 → 19 W。
  電源から SSH まで約 170 秒。うち BIOS が約 110 秒で、その間は映像も出ない（メモリは 32 GB × 4 枚、Crucial と SanMax の混在）。MRC Fast Boot と Fast Boot は最初から有効で、POST Delay Time は 3 秒。残りの原因は未確認。rEFInd の画面が 5 秒。Linux 側は initramfs から systemd に移る所で約 20 秒止まり、ログから原因は読めない（未調査）。
  1 度だけ Linux の再起動が終了処理の最後で固まった（JetKVM の ATX の Reset で戻した）。その後 3 回は再現しない
- **屋外のライブの束調整は `BA=0.5 BA_ACC=30 BA_ALPHA=60`。** 既定の強さ（`BA_ACC=3 BA_ALPHA=10`）は屋内の Tiny Whoop に合わせたもので、屋外のレース機では描く点がかえって悪くなる
  （d05 p50 0.13 → 0.19 m、揺れ 0.7 → 1.4 cm）。4 人同時・`RENDER=1`・Linux で、ページの描き方（100 ms 遅らせた内挿）を `tools/dvr/rt_eval_draw.py` で再現して、同じ答えの「直しなし → 直しあり」を比べた：

  | 描く点 p50 / p90 | d05 | KNT | SENA | SAQ |
  |---|---|---|---|---|
  | 既定の強さ | 0.13 / 0.49 → 0.19 / 0.60 m | 0.43 / 1.94 → 0.45 / 1.99 | 0.14 / 0.75 → 0.19 / 0.72 | 0.23 / 1.33 → 0.23 / 1.21 |
  | **`BA_ACC=30 BA_ALPHA=60`** | 0.13 / 0.47 → **0.12 / 0.40 m** | 0.43 / 1.93 → **0.36 / 1.85** | 0.14 / 0.76 → **0.13 / 0.63** | 0.23 / 1.07 → **0.20 / 1.02** |

  揺れも 1〜2 割減る（d05 p90 2.7 → 2.3 cm）。重さは 1 周 p50 9.9 → 11.3 ms、d05 59.5 → 58.1 Hz、電力 +4 W。さらに緩める（80 / 150）と窓 1 秒（1 周 13.1 ms）は、回ごとのぶれの範囲で差が出ない
- **裏で起動した tracker は止めの合図を受けなかった。** 非対話のシェルが裏で起動したプロセスは SIGINT を無視する設定で始まり、Python はそれを引き継ぐ。
  `rt_track.py` で SIGINT と SIGTERM を明示的に受けるようにした（止めると集計が書かれる）
- **`CUDA_SYNC=block` は Linux でも効く**（4 人で CPU 49 → 16 W）が、1 周が 11.5 → 26 ms と倍以上になって 1 人あたりの Hz が落ちるので使わない

DELTA 2 Max の AC は変換効率 86%・AC を入れているだけで 23 W（[thesolarlab](https://www.thesolarlab.com/review/ecoflow-delta-2-max-review)、
[diglloyd](https://windinmyface.com/blog/2025/20250116_1012-Ecoflow-Delta2Max-AC-conversion-efficiency.html)）なので、使えるのは約 1,760 Wh。
本体の残り（マザーボード・メモリ・ファン）を 40 W、電源の効率を 90% と仮に置く（どちらも未測定。モニターや受信機は別。走らせながら DELTA の出力表示を読めば確かめられる）と、4 人が飛び続ける形の 282 W は約 360 W で 4.9 時間、準決勝の流れ（`SIGNAL=1`・Linux・NDI）の 122 W は約 180 W で 9.7 時間。
**大会 1 日ぶんは、電池 1 台の 84〜91%。** JDL 2026 Round 6 の配信の録画 2 本（予選とファステストアタック 4 時間 37 分、準決勝と決勝 3 時間 13 分、計 7 時間 50 分）の上段の 4 マスに tracker と同じ映像の判定を掛け、映像のあるマスの数ごとの実測の電力（0：直後 20 秒は 71 W、あとは 32 W。1 / 2 / 3：110 / 166 / 202 W。4 は未測定で 238 W と外挿）を掛けた（`tools/dvr/rt_day_gate.py` → `rt_day_power.py`）。
映像のあるマスが 0 / 1 / 2 / 3 / 4 の時間は 34 / 19 / 16 / 16 / 16%、GPU＋CPU の平均 131 W・1,024 Wh、壁では 190 W・1,486 Wh（84%）。配信が別の画面を映していた時間（13%）もレース中と同じだったとすると 145 W・1,608 Wh（91%）。合間の見張りは 2% しか効かない。**Event VRX の NDI は 60 fps だが、tracker が 30 fps に間引く**（`MAXFPS`、操作ページは「60 fps → 30」と出す）。間引かないと 3 マスで GPU 125 → 172 W（＋35%）、処理 43〜54 枚/秒、届いてから位置まで p50 25〜32 ms（30 fps では 19〜28 ms）。ページは 100 ms 遅らせて内挿で描くので 60 fps ぶんの答えは要らない。ここの数字は全部 30 fps。準備や片付けで映像の無い時間は 1 時間につき約 4%。

- **照合はまとめて呼ぶ。** LighterGlue は 1 組 14 ms、8 組を 1 回のバッチで 19 ms。命令を出す回数で時間が決まっている。
  バッチには点の刈り込み（`width_confidence`）を切る必要がある（B > 1 で `The size of tensor a (2048) must match the size of tensor b` で落ちる）。
  点数は TOPK にゼロ埋めし、埋めた点への対応は捨てる
- **GPU と CPU を重ねる**（`PIPE`）：d05 だけで 0 → 28 Hz、1 → 43 Hz、2 → 56 Hz。予測が 1 周古くなっても描く点は悪くならなかった（0.61 → 0.55 m）
- **PnP は PoseLib**（`PNP=poselib`、既定）。走行中の実際の入力（d05、キーフレーム 2 枚分の対応 p50 903 点、inlier 33%）で MAGSAC は 1 回 11 ms だった
  （前に測った 3 ms は 400 点の合成データ）。時間は MAGSAC の採点に掛かっていて、反復を 500 → 200 にしても 10.8 ms。PoseLib（P3P＋LO-RANSAC）は 2.8 ms で、
  答えも参照に近い（d05 p50 0.24 → 0.19 m）。GIL を手放すので 4 本のスレッドで 3.8 倍に並ぶ。**PoseLib は既定で成功確率の要る回数の 3 倍を回す**
  （`dyn_num_trials_mult` 3.0）ので 1 倍に落としてある（`PL_DYN=1`。3.8 → 2.8 ms、精度は同じ）。効かなかった：照合のスコアの順に標本を取る PROSAC（反復が減らず外れが増えた。
  LighterGlue のスコアの高さと inlier かどうかが結びついていない）、OpenCV の USAC_FAST（5 ms）。2 枚のキーフレームで同じ画素に対応した重複点（24%）を外すと 3.1 ms だが、
  答えが動くので入れていない（真値では未確認）。その前は SQPnP の RANSAC（12 ms、外れ 70% の合成データで 0/101）から MAGSAC（99/101）に替えて見失いが減った
- **照合と XFeat は TensorRT のエンジンで回す**（`GLUE=eng/glue_mix.engine XFEAT=eng/xfeat_fp32.engine`、`tools/dvr/rt_trt.py`）。tracker の形は 640×480・2048 点で
  固定なので、バッチだけ可変のエンジンにできる。TensorRT 11 は ONNX の型どおりに組む（FP16 のフラグが無い）ので、精度は書き出す型で決める。
  照合の `glue_mix`（6 層は fp16、割り当ての log-softmax と相互判定は fp32）は 8 組 19 → 10 ms、1 組 9〜13 → 3.5 ms、PyTorch との対応の一致（IoU）p50 0.92 で通しの精度は同じ。
  全部 fp16 も同じ。**fp32 のエンジンはバッチで PyTorch より遅い**（8 組 24 ms）：PyTorch 版も attention は fp16（kornia の flash の経路が `.half()` する）。
  XFeat は detectAndCompute の可変長の段（NMS の `nonzero`、候補の `argsort`）を「全画素の点数（落ちた画素は -1）から topk」にして固定した（点の一致 IoU 0.99、記述子のコサイン 1.000）。
  本体の短縮は 4 枚 4.3 → 2.7 ms と小さく、効くのは点を 2048 の固定長のまま有効マスクつきで照合へ渡すこと：フレームごとの切り出し（1 枚ごとに GPU を待つ）と
  照合前の詰め直しが消える（照合は点の順序に依存しないので、マスクした点をゼロにして対応を捨てれば同じ）。照合の結果も組ごとでなく 1 回で CPU に移す。
  XFeat の fp16 は速くならない（チャンネルの少ない畳み込み）。書き出しで直したもの：kornia の `unflatten(-1, (heads, -1, 3))`（TensorRT が読めない）と `repeat_interleave`
  （形を証明できない）は posenc と attention を定数の形で書き直し、XFeat の `BatchNorm2d(affine=False)` は 1 と 0 の重みを明示（書き出しが CPU に重みを作って
  `Expected all tensors to be on the same device` で落ちる）。比較の記録は win4090 の `rt/trt/`（出力 `rt/trt/out/`、`rt/trt/office/`）
- **LighterGlue の PyTorch の fp16（`mp`）は使わない。** 解けるフレームが 1,630 → 761 に減り、速くもならない。悪いのは autocast のほうで、TensorRT の fp16 は上のとおり問題ない
- **見失いは時間で数える。** 試した回数で数えると、速くするほど短い時間で諦め、重い再局在化（約 100 ms、その間フレームが落ちる）
  に入ってまた見失う。fp16 と重なって、原因を取り違えかけた
- **描く点のギザギザは答えそのものの揺れ**（1 つ 0.25 m）。外挿の速さを最近の速さで抑えても変わらなかった。カルマン＋段差を溶かすと
  0.86 → 0.70 m、揺れ 18 → 11 cm。それ以上は軌跡を遅らせて描き直すほうが効く
- 効かなかった：予測姿勢で対応を先に絞る、OpenCV のスレッド数。特徴点 512 は減らしすぎ（Mac で見失いが増えた）。
  特徴点 1,024・キーフレーム 1 枚は Mac では効いた（上）。4090 で最初に測ったときは fp16 が入ったままだったので、4090 での答えはまだ無い
