# MultiGP Championship 2026 — 公開 DVR だけからコースと全 Round の飛行経路を作る

スキャン無し。MultiGP が公開している pilot-review の 8up 録画（8 人の HDZero DVR を 1 本の mp4 に並べたもの）だけから、
SfM でコースの地図を組み、3DGS を学習し、各 Round の各 pilot の経路を地図に合わせて `viewer/mgp.html` で再生する。
道具は `tools/mgp/`、結果の小さい JSON は `data/mgp/`、映像・画像・地図・3DGS は git の外（`build/mgp/`、Studio と rt4090 の作業場）。

## 素材

- 一覧：`https://race-dvr-public.s3.us-east-1.amazonaws.com/pilot-review/manifest.json`（UTF-8 BOM 付き。`encoding="utf-8-sig"`）。
  `url` を `pilot-review/` に足すと mp4。10/15 の Bracket は 77 本（20 MB 未満の空録画を除く）、計 37 GB。`alias` の「Bracket · Race N」は
  公式の Qualifier Round N と同じ（公式ラップで 2 回確かめた）。同じ N が 2 本あるもの（76、83）は後のほうが取りこぼされる。
- 録画は 1920×1080 60 fps、3×3 の 640×360 マス、各マスの中央に 4:3 の 480×360 の映像（x オフセット 80）。マス 8 は「RACE N」と時計。
  Race 17 以降は各マスの上 ~40 px に放送の帯（順位・国旗・pilot 名・LAP n・ラップタイム）。**帯と映像のマスがずれる Round がある**（Q44：
  EL-MIDERRO の帯はマス 7、映像はマス 6）。Race 1〜16 には帯も時計も無い。
- 落ちた機体のマスは止まった絵か砂嵐。砂嵐は隣の画素との差の平均が 23〜26（ふつうの映像は 3〜4）。
- 公式の記録：`https://mgp.livefpv.com/results/`（LiveFPV）。各 Round の結果ページの JS に `racerLaps` としてラップごとの値が埋まっている。
  `tools/mgp/livefpv_laps.py` が全 Round を `data/mgp/livefpv_laps.json` にする。**1 周目はその pilot 自身のホールショット（スタートゲートの
  最初の通過）から数える**。lapNum 0 の行がその通過。MCKFPV が総合 1 位（2 周連続 33.184 s、Round 44）。
- コースの配置：VelociDrone の公式トラック ID 2189「2026 MultiGP Championship Track」（Velbd の `getOfficialTracks` で取得、AES の復号は
  Velbd の `scripts/download-trk.ts` と同じ）。cm 単位、ゲート 32、地面のライン（prefab 303）と Speed Trap の矢印（132）。
  **VelociDrone は ground truth ではない**：縮尺と向きと順番の当たりに使い、ゲートの位置と大きさはスキャンから測る（`gates_from_map.py`）。
  実物のゲートの開口は約 2.8 × 1.85 m で、VelociDrone のゲート（2.17 m 幅）より広い。配置のずれは 0.3〜0.9 m。

## 通し

```
# Studio（~/mgp、env/ は uv の venv：pycolmap 4.2.1・opencv・ocrmac）
./extract_all.sh all 30 4                 # 全 Round を 30 fps で q<NN>_s<slot> に切り出す（aliases.json から Round 番号）
env/bin/python ocr_scan.py raw/<mp4> ocr/<name>.json 10      # 帯と時計の OCR（macOS Vision）
# rt4090（CPU、nice -n 10。/mnt/data/mgp が作業場。GPU は ZTMY のセッションと共用）
~/mgp/chain.sh chain vocab256k.bin chain/models/m01 r1404 r1451 ...   # レース 1 の地図に 1 レースずつ足す
~/mgp/env/bin/python ~/mgp/localize.py <map_dir> <out> images/q44_s0 ... -j 5   # 全フレームの位置合わせ
# Mac
uv run --with numpy --with scipy python tools/mgp/poses_from_loc.py <top_frame.json> <fit.json> <poses_dir> <loc.json>=s0:0 ... --smooth
uv run --with numpy python tools/mgp/name_slots.py 44 <poses_dir> build/mgp/poses/q44 60        # ラップの並びでマス→pilot
uv run --with numpy python tools/mgp/make_mgp_race.py build --round 44 --poses build/mgp/poses/q44 --out build/mgp/q44 \
    --fps 60 --margin 2.5 --min-gap 10 --ocr-dir /nonexistent --gates build/mgp/gates/gates_1404.json --crop-top 40 [--clips]
```

- `extract.py`：2 回読み（1 回目で統計と live 判定、2 回目で書き出し）。メモリは 1 本 1.7 GB。**前の版は全フレームを載せる作りで、6 本並べて
  Studio（64 GB）を落とした。** ファイル名は `(i + 0.5) / fps` の時刻（ffmpeg の fps フィルタは区間の真ん中を取る。3 fps なら 167 ms）。
  OSD は時間方向の分散が小さい画素、プロペラは下の両隅の彩度の高い非緑、上の帯 42 px、魚眼の隅をマスクにする。
  live 判定：明るさ > 30、8×8 のブロック平均の 1/3 秒前との差 > 6（墜落して止まった絵の圧縮ノイズを動きと数えない）、隣の画素との差 < 12（砂嵐）。
- SfM：pilot のフォルダごとに OPENCV_FISHEYE を 1 台（初期値は上側のレンズ寄り `195,197,240,180,0.155,-0.09,0.026,-0.003`）。
  **15 レース 2.4 万枚をまとめて組むと壊れる**（global はかけらだらけ、incremental は 1 つになるが同じ印刷のゲートの取り違えで中身が散る）。
  効くのは、きれいな 1 レース（レース 1、1,199 枚、3 fps、全組み合わせ照合で 1 モデル）に、`register.py`（既存フレーム固定、
  `abs_pose_min_num_inliers` 30、sequential ＋ vocab tree）で 1 レースずつ足す `chain.sh`。1 レース 35〜55 分だが、データベースに前の試行の
  照合が残っていると、それらも一緒に登録される（14:04 の 1 本で 2 時間 48 分、7,523 枚）。pycolmap の `incremental_mapping` は `input_path` が
  あると出力先の直下に書く（`0/` を作らない）。
- レース 1 の地図の欠陥と修理（`mapfix.py`）：同じ印刷の窓ゲート 2 つ（8 番と 32 番）の取り違えで、1 周に 1 回 60〜75 m 飛ぶ区間が 3 人とも
  あった。3 fps で 60 m/s を超える歩幅で見つけて、52 枚を外して束調整して登録し直すと 0 回になる（m01）。
  **3DGS で見える「幽霊」は 2 つとも本物だった**：TBS の列の裏 0.4 m に立つ MultiGP のバナーの柵と、列の 3.5 m 先の次のゲート（29 番）。
  斜めからしか見ていない物は薄く描かれる。点の観測元を pilot × 周回で分けて、1 つの組だけが見ている升が無ければ写しではない。
- 位置合わせ（`localize.py`）：OpenCV の RootSIFT（COLMAP の記述子と直接合う）、地図のキャッシュ `loc_cache.npz`、等速予測で近い地図画像 6 枚、
  失敗時は全点の記述子で再探索。同じレースなら 15 fps の 98〜99% が乗り、前後 7 フレームのぶれ 6 cm（p50）。
  **別の時刻の映像は乗りにくい**：14:00 の Q44 を 10:34 の地図に合わせると 52〜62%、一致点 35〜40（同レースは約 200）。レンズのせいではない
  （純正レンズは全マスで最下位。`--lens auto` が純正・上側から選ぶ）。日の向きと影の違い。時間帯の違うレースを地図に足して埋める。
- `poses_from_loc.py --smooth`：60 fps の格子にジャークのペナルティ付きで当てはめ、Huber で外れを落とす（2 m 超は捨てる）。`LAM` 2e-4 が
  観測からのずれ p50 9 cm、加速度の最大 130 m/s²。地図の取り違えは先に捨てる：80 m/s を超える（4 m 超の）動きを「ありえない跳び」とし、
  1 枚だけの外れ（抜けば前後が普通の速さでつながるもの）を先に、次に跳びで切り離された 15 コマまでの短い塊を短い順に捨てる。
  距離だけの判定（往復する 15 m 超の跳び）は、空白をまたいだ正しい直線（スタート直線は 44 m/s）や外れの隣の正しい区間を捨てて
  ラップを落とした（Round 54・60・61・82）。**予選の線を作り直したら、前の版とラップを人ごとに比べる**（良くなった数だけでなく悪くなった数も）。
- **マス→pilot は帯でも OSD でもなくラップの並びで決める**（`name_slots.py`）。OSD の機体名は 3 倍に拡大しても読めない。Q44 の 5 人とも
  公式ラップと 0.04 秒で一致。アナログの pilot は位置合わせできないので自然に外れる（Saqoosha：アナログは無視でよい）。
- `make_mgp_race.py`：race.json は FDF の race ページの形＋ `official_laps`・`position`・`video_size`・`checkpoints`（`--gates` で
  スキャンの測定値）。クリップを作り直さないときも `--crop-top 40` が要る（無いと `video_size` が 360 に戻る）。スタートの合図は
  「いちばん早いホールショットの 2 秒前」の仮置き（帯の時計は前のレースの帯が残る時間があり使えない）。
- 3DGS：spirula `train in-the-wild`、`--disable-viewer 1`（7007 番の取り合いで落ちる）、マスクは `masks/` を自動で使う。座標は SfM のまま
  なので `to_metric.py`（地面の面 ＋ 2D 相似）→ `ply_to_web.py`（x 軸 −90°、y 上）→
  `npx -y @playcanvas/splat-transform@3.4.2 -w in.ply out.sog`。1,199 枚・30k 反復・200 万 splats で 12 分（GPU 独占時）。

## 夕方と夜の決勝（B・A・E Main の Final）

昼の地図と 3DGS には、夕方以降のフレームがほとんど乗らない（夜は SIFT も、3DGS を描いて MASt3R で照合しても 0 点）。夕方から夜までの
録画を 1 つの地図に組み、昼の決勝のフレームを基準点にして昼の地図の座標に載せる。道具は `tools/mgp/night_*.py`、作業場は rt4090 の
`/mnt/data/mgpwork/night/`。

```
# Studio：4 分割の録画を切り出し（extract.py LAYOUT=4up）、原寸のコピーを作る
env/bin/python fullres_frames.py raw/<mp4> <extract.py の out> <out> <fps>
# rt4090（~/mastenv、hloc）：照合（GPU）→ 地図（CPU）
python night_sfm_hloc.py sk/sfm sk/images sk/masks <feed>[:<n 枚に 1 枚>] ...
python night_db_srf.py sk/sfm database_v1.db database_srf.db      # カメラを SIMPLE_RADIAL_FISHEYE にした複製（下の「カメラのモデル」）
MAPPER=incremental REUSE_DB=1 DB=database_srf.db BA_RATIO=1.4 PRUNE_BA=1 SNAPSHOT=600 [INPUT=<途中保存>] python night_sfm_hloc.py ...
python night_model_check.py <model>; python night_model_plot.py <model> <out.jpg> <feed> ...    # 外れ値の検査、真上の図
QCAM_FROM=dayloc/rounds/fd_loc NRET=20 python night_localize.py sk/sfm <model> sk/images sk/masks sk/loc_fd fd_s0:3 ...   # 基準点
WIN=0.7 NRET=5 python night_localize.py sk/sfm <model> sk/images sk/masks sk/loc2 fb_s0 ...                             # 決勝の全フレーム
FRAME=../top_frame.json FIT=../fit01.png.json COURSE='dayposes/*/*.json' ANCHOR_PREFIX=fd_ LOCAL=1 \
  FOLD=-138,-104,-60,-22 FOLD_K=15 FOLD_MIN=4 python night_anchor.py <results> <inliers> sk/anchor5 <day loc dirs> -- fb_s0 ...
# 折りたたみの直し：スキャンに描いて確かめる → 夜の姿勢を残す → 反対の弧に置かれたコマを前後からたどる（A・E は夜、B は昼のスキャン）
python ../night_refine.py <scan.ply> ../top_frame.json ../fit01.png.json sk/sfm/models_srf2 sk/anchor5 sk/images sk/masks720 fold/refine fa_s1 ...
python ../night_keep.py fold/refine ../top_frame.json ../fit01.png.json fold/keep fa_s1 ...
JUMP_MPS=1000 ALL=1 MAXGAP=150 python ../cpr_gap.py <scan.ply> ../top_frame.json ../fit01.png.json fold/keep fold/refine/frames fold/gap2 fa_s1 ...
FIELD=sk/anchor5/field.npz FRAME=../top_frame.json FIT=../fit01.png.json COURSE='dayposes/*/*.json' FOLD=-138,-104,-60,-22 LOC=fold/gap2 STATIC=0.3 \
  python night_gs_dataset.py <model> sk/anchor5/transform.json sk/images sk/masks720 ~/mgp/gsnight4 e2100 na11 fa fe      # → spirula
python night_warp.py splat sk/anchor5/field.npz <splat.ply> <out.ply>      # → to_metric.py → ply_to_web.py → .sog
python splat_box_merge.py gs/t4/.../splat.ply gs/t5/.../splat.ply ../top_frame.json ../fit01.png.json gs/t45.ply  # 箱の中だけ t5
# Mac（fold/gap2 の loc JSON を build/mgp/loc/q90X/ に置いて）
ID=fa VF=<mp4> LAYOUT=4up SCENE=MGP-Champs-2026-night SKY=sky-night.jpg bash tools/mgp/round_mac.sh 905
```

- **特徴**：夜の映像は色つきの小さな四角（圧縮のブロック）だらけで、SIFT だと A Main レース 11 の 2,471 枚中 445 枚しか地図に入らない。
  ALIKED + LightGlue を原寸（720×540、4 分割のマスを縮めない）・4,096 点で使う。0.5 秒離れた組の一致点の中央値は SuperPoint 13、
  ALIKED（480×360）30、ALIKED（原寸）48。CLAHE やノイズ消し（中央値・膨張収縮）は ALIKED を悪くする。
- **つなぎ**：10/17 の 18:58〜22:54 の 9 本（B・A 決勝を含む）と 10/16 の E 決勝を 1 つの地図に入れる。となりの録画どうしは明るさが近く、
  30 点以上一致する組が 1,000〜7,000 ある。昼 12:30 の D 決勝のフレームも、この地図に 96% 登録できた。
- **カメラのモデル**：OPENCV_FISHEYE の歪み 4 つをマスごとに自由にすると、夜は遠くの照明ばかりで決まらない。束調整が線形ソルバの失敗を
  繰り返し、1 回に 55 分はまった。SIMPLE_RADIAL_FISHEYE（焦点と歪み 1 つ、f 320・k 0 から）にする（`night_db_srf.py`）。
- **GLOMAP は使わない**：誤差 1.15 px で向きも合うのに、どのマスのカメラも 1 点のまわりに星形に潰れた（2 回）。1 枚ずつ足す方式
  （incremental）に、全体の束調整の間引き（BA_RATIO 1.4、`ba_global_ignore_redundant_points3D`）を付ける。途中保存から続けると
  （`input_path`）、地図は `models/0/` でなく出力先の直下に書かれる。
- **検査**：続く 3 コマのまん中が前後の中点から外れたら外れ値（`night_model_check.py`）。「移動量が中央値の 3 倍」で数えると、発進前に
  止まっているコマで中央値が 0 近くになり、正しい地図を 12〜17% と誤って出す。正しい地図は 0〜3%、潰れた GLOMAP は 18〜20%。
- **基準点**：夕方のフレームの昼の SIFT の位置は、一致点の中央値 28 でほとんど外れている（焦点が −14 億 px に発散したマスもある）。
  基準点は昼 12:30 の D 決勝（一致点 53〜65）だけにする。
- **ゆがみ**：1 つの相似変換だと、発進の場所の近くは 0.3 m で合うのに、遠い区間ほどずれる（3.6 m から数十 m）。D 決勝の基準点ごとに
  近くの 30 点で相似を RANSAC で求め、点ごとに近い 4 つを混ぜる（`night_warp.py`）。基準点をその近傍から予測した誤差は中央値 0.17 m、
  75% で 0.43 m。3DGS も同じ場で splat ごと動かす。
- **折りたたみ**：コースの左の中ほど（左の「？」形の鉤と右の弧、page world で x −138〜−104、z −60〜−22）を、夜の地図が
  1 か所に重ねた。同じ絵柄のゲートの取り違えらしい。D 決勝を登録しても 2 本の弧が 1 本の帯になり、`FOLD`（近くの基準点を本当の位置で
  2 群に分け、つながりが最短の並びを動的計画法で選ぶ）でも斜めの往復が残った。直したのは地図でなく決勝の姿勢：昼の形に夜の色を
  付けたスキャン（下の t4）では 2 本の弧が分かれているので、全コマをいまの姿勢で描いて MASt3R で照合する（`night_refine.py`）。
  正しい弧に置かれたコマは数百〜数千点で合い、反対の弧のコマは 6〜30 点か、PnP が 5 m 以上動かす。照合できなかったコマのうち
  箱の中のものだけを未解決にし（`night_keep.py`）、`cpr_gap.py`（`ALL=1`）が前後から描いて照合しながらたどる。B 決勝（夕方）は
  昼のスキャンで照合する（夜のスキャンでは半分外れ、昼なら 584〜5,505 点）。たどれずに残るコマは、なめらかにする段がつなぐ。
- **照合した姿勢をそのまま使わない**：1 コマずつ描いて照合した位置は、夜の地図の位置よりコマごとのぶれが大きい（となり 2 コマの中点
  からのずれの中央値 0.67 m 対 0.27 m、一致点 1,500 以上でも 0.62 m）。2 つの差のうちゆっくり変わる部分（1 秒の中央値）は中央値 0.13 m、
  上位 1 割で 0.42 m しかない。照合は確かめに使い、確かめたコマは夜の姿勢にその遅い差だけ足す（`WIN`）。箱の外で照合できなかった
  コマ（暗い絵、ぶれた絵）は夜の姿勢のまま残す。
- **跳びの判定を切る**：`cpr_gap.py` の「前後どちらへも秒速 45 m を超える跳び」は、`ALL=1` だとコース全体にかかり、スタート前の
  直線（秒速 44 m）の正しいコマを捨てる。A 決勝の MCKFPV は 3 周目のゴールの前の 6 コマを失い、周が数えられなくなった（予選の
  Round 61 MINJAE_ と同じ）。決勝は `JUMP_MPS=1000` で回し、ありえない速さの外れは `poses_from_loc.py` に任せる。
- **カメラ**：決勝の loc JSON のカメラは昼の位置合わせの残りで、夜のマスでは焦点が 8 億 px に発散している。描いて照合するときは夜の
  地図がマスごとに求めた SIMPLE_RADIAL_FISHEYE を使う（`night_refine.py` が JSON に書き、原寸 720×540 のフレームと `masks720` を読む）。
- **照合の速さ**：MASt3R の `fast_reciprocal_NNs` は 8,192 行ずつの小さな塊で GPU に何千回も命令を出し、1 組 0.16〜0.27 秒（4 本で
  GPU を分けると 1.4〜2.1 秒）かかる。推論そのものは 0.08 秒。`cpr_gap_lib.reciprocal_nns` は同じ組（99.9% 一致、違いは同点の取り方）を
  大きな塊で 0.07〜0.12 秒で出す。並べるのは 3 本まで（1 コマ 0.9 秒。4 本だと取り合って 2.6〜3.0 秒）。
- **結果**：決勝のラップが公式と一致した。B 決勝は 0.03 秒以内（REDSKY の 3 周目だけ 0.078）、E 決勝は 0.054 秒以内、A 決勝は
  MCKFPV 0.089 秒・KILLIANFPV 0.183 秒以内。前の姿勢をいまの `poses_from_loc.py` に通すと REDSKY の 3 周目が消えるが、直した姿勢では
  全員の全周がそろう。
- **Main の勝ち抜きのレース**（Round：A 911〜、E 931〜、D 951〜、C 971〜、B 991〜 ＋ Race 番号。ラップは `data/mgp/livefpv_<x>_main.json`
  から `livefpv_finals.json` へ）。録画↔レースは公式の終了時刻＋1 時間と AWS の一覧の録画の開始時刻で引き、1 本に何レースも入った録画
  （E Race 5〜7、D Race 4・5）は Round ごとに同じ位置合わせから組む（`make_mgp_race.py` がレースごとの通過を選ぶ）。A Main の 19:49〜20:15 と
  B Main の 16:41〜16:53 の録画は、やり直す前の走りで公式ラップに合わない。A Race 4 は 21:15（帯に CRASH / LAND NOW）でなく 21:32。
  D Race 1・2 は AWS に録画が無い。
  - 夜（A・E）と夕方（B）：rt4090 の `main_night_race.sh <id> <録画> [スキャン]`（rt4090 の `mgp_studio/mains/` に置いて回す） → 切り出しと原寸 → `night_race.sh`（夜の地図、
    折りたたみの箱の中だけ描いて確かめる `ONLY_BOX`、地図に無いマスのカメラは `night_race_prep.py` がマスの位置ごとの中央値：4 番目のマスだけ
    焦点 274〜278、ほかは 300〜318）→ `drop_static.py`。B は昼の地図に 641 中 52 しか乗らず、夜の流れで確かめを昼のスキャンにする
  - 昼（C・D）：`main_day_race.sh <id> <録画>` → 切り出し → `localize.py`（`mapfinal`）→ `drop_static.py`
  - Mac：loc JSON を `build/mgp/loc/q<N>/` に置いて `round_mac.sh`。3 本並べて組める（`rounds.json` は鍵つき、クリップの符号化は `CLIP_THREADS`）
- **4 分割の切り抜き**：4 分割の録画は、マスの中の映像の位置と幅がマスごとに違う（左から 90〜121 px、幅 644〜738 px、日によっても違う）。
  決まった切り抜きだと片側に黒い帯、反対側が欠けた。`feed_rects.py` が録画ごとに時間で動く画素の四角を測り、`make_mgp_race.py` がそれで切る。
- **ゴール後のカード**：ゴール後、4 分割の録画は各マスに順位カード（写真・順位・時間）を重ねる。夜の地図はそのカードの絵を 1 か所
  （track frame で x −123.8、y 6.5、高さ 3.4 m）に登録していて、カードのコマが一致点 1,000 前後でそこに置かれ、ゴールした全員がそこへ
  飛んだ。背景の機体が動いていても照合はカードに勝つので、絵が止まったかの判定では拾えない。`drop_static.py` が、止まった絵、位置が
  10 cm 以内で 6 コマ続くもの、確かめのない夜の姿勢でその場所から 1 m 以内のものを外す（63 人中、そこに残る人 0）。
- **夕方〜夜の予選（105〜120、10/15 19:07〜20:25）**：8 分割の録画。昼の地図には 0〜15% しか乗らず、夜の流れに通した
  （`LAYOUT=8up main_night_race.sh nq<N> <録画> <スキャン>`）。8 分割は抜き出した 480×360 が原寸で、カメラは夜の地図の全部のマスの
  中央値を幅で縮めたもの（焦点 205）。多くのフィードで 4〜9 割が乗る。確かめに使うスキャンは 105〜108 が昼、109 以降が夜（`gs/t45.ply`）、
  見せるシーンも同じ分け方。名前は帯（`LAYOUT=8up name_from_bars.py`）で、どれも一致度 1.00。載った 81 人（公式の出走 87 人、
  録画にマスの無い人がいる）のうち 70 人は全周が公式と 1 秒以内。106 BENNETT_FPV は公式 0 周だが軌跡は 3 周（ほかの 6 人は自分の
  公式ラップと 0.1 秒以内で合うので、マスは消去法で決まる）
- **ラップの数え方**：スタートゲートの面を前向きに、ゲートの半幅＋2.5 m・高さ＋2 m の内側で通った時刻（`make_mgp_race.py` の
  `crossings`）。前の 1.5 m では夜の地図の軌跡が 0.1〜0.3 m 外れて落ちた。10 秒より近い 2 回目は数えない（どの周も 17 秒より長い。
  離陸でゲートをくぐって戻ると 3.6 秒の周ができた）。4 分割はゴールの瞬間に順位のカードがマスを覆うので、軌跡がゲートの数コマ手前で
  終わる。最後の 0.2 秒の速さで 0.5 秒まで先へ伸ばして通過を探す（そうして出たゴールは公式と 0.11 秒以内）。全 192 Round で、
  公式に近づいた 31 人、遠ざかった 2 人。その 2 人も新しい数のほうが軌跡に合う（935 RECONFPV の 2 → 1 周は、前の 2 周目が 9.8 秒の偽の周。922 REDSKY は公式 0 周だが、軌跡は 22.6 秒で 1 周してゲートの 0.5 秒手前で切れる）
- **名前**：ラップの並び（`name_slots.py`）で決まらないマスは、消去法（`name_by_elimination.py`：残りのマスと残りの人が 1 つずつ）、
  次に 4 分割の帯の名前（`name_from_bars.py`：macOS Vision で各マスの帯を Round の窓の 3 か所で読む）。足したら `make_mgp_race.py` を
  直接回す（`round_mac.sh` は名前付けからやり直して、足した名前を消す）。10/16 の E Main の録画には帯が無く、Race 2・3・5 は決まらない。
  A 決勝の YUKI のマスは録画に映像が無い。
- **夜の 3DGS は昼のスキャンの形に夜の色を付けて作る**：夜の 4 本（21:00、22:09、A・E 決勝）のフレームだけで学習すると、暗さとノイズで
  形が決まらず、どの視点でも色のにじみと霧になった（2,741 枚、PSNR 22.3）。昼のスキャン（200 万 splat）を `--init-ply` に、
  位置と向きを止め（`--means-lr 0 --means-lr-final 0 --quats-lr 0`）、大きさと透明度はゆっくり（`--scales-lr 0.001 --opacities-lr
  0.005`）、`--cap-max 2000000 --num-iterations 15000 --distraction-robustness strong` で色だけ学ばせる（17 分、PSNR 24.9）。
  ゲートの枠、地面の LED のライン、照明が出る。カメラは `night_gs_dataset.py` に `FIELD`（場所ごとの補正）と `FOLD`（折りたたみの
  区間のフレームを外す）を付けて昼の座標に置く（t4）。
- **t5 と合成**：折りたたみを直した決勝のコマ（`LOC=fold/gap2`）を足し、0.5 秒たっても絵がほとんど変わらないコマ（`STATIC=0.3`：
  ゴール後に録画に重なる順位カード、止まった機体）を外して 7,628 枚・30,000 回（1 時間 33 分）。t4 はカードを景色として覚えていた。
  t5 は折りたたみの区間の二重のゲートと LED の線が出るが、ほかの場所は t4 のほうが良い（Saqoosha の目視）。どちらも位置を止めて
  色だけ学ばせたので、`splat_box_merge.py` で箱（2 m 広げる）の中を t5、外を t4 にして 1 つにする。
  比べるときは `EXTRA_SCENES` で上げた `-t4` / `-t5` を `?scene=` で開く。
- rt4090 の NTFS（ntfs3）の上では、シンボリックリンクが 41 本目から作れなくなった（ENOENT）。10/19 からは `/mnt/data` が ext4 なので起きない。
- hloc：`localize_sfm.main` は解けないフレームを、最初に引いた画像の姿勢に置いてしまう（`night_localize.py` は中の関数を直に呼ぶ）。
  `import_features` などは開いた `pycolmap.Database` を受け取る。MegaLoc は `torch.hub` の信頼の確認で止まるので、一度
  `trust_repo=True` で読み込んでおく。

## ページ（`viewer/mgp.html`、`src/mgp.ts`、共通部は `src/racekit.ts`）

- データ：`<data>/rounds.json`（Round の一覧）、`<data>/<round>/race.json`＋ `dvr_<pilot>.mp4`＋ `audio.m4a`、スキャンと空は
  `<data>/scene/<scene>.sog`・`<data>/sky.jpg` を全 Round で共有。Round が `race.json` の `scene`・`sky` で別のスキャンと空を名指すと、
  そこだけ替わる（夜の決勝：`MGP-Champs-2026-night`・`sky-night.jpg`）。開発用は `viewer/public/mgp-dev/`。
- 決めたこと：右端に DVR の列（常に 1 列、全高、同じ大きさ、追っている pilot は枠で一番上）、キャンバスはその左を全高、見出し・順位表・
  操作のバー（左下）は浮かせる。Orbit・Top は空き領域（キャンバス − 順位表 − バー）に収める（主点をずらす投影）。
  Orbit の距離は寄り 2.5 s・引き 1 s の時定数、2 s 先読み、3% の不感帯。Top は公式図と同じ向き（右 = world −z、上 = world −x）、
  コースの中心をキャンバスの中心に、地面＋12 m より上の splat を切る（空の霧）。スキャンがあるときはゲートの枠を描かない。
- 軌跡と球は不透明・深度書き込みで splat の前の不透明パスに描く（手前の splat に隠れる）。薄れは alpha-to-coverage（MSAA 4 なので 5 段階の
  細かい縞。Saqoosha はこれを選んだ）＋幅の絞り 5 → 1 px。ディザは点々が目立つので不採用。
- 1 周目のタイマーは `holeshot_at` から。クリップの帯は切り落とす（ページのチップと二重になる）。

## 検証で使える基準値

- レース 1 の 3 人：公式ラップとの差 0.15 s 以内（3 fps でも 15 fps でも）。Q44 の MCKFPV：16.539 / 16.685 / 21.058 s（公式 16.509 / 16.675 / 21.021）。
- 縮尺：レース 1 の地図は 1 単位 = 7.37 m（地面のラインへの chamfer 1.4〜1.6 m）。ゲートの高さがスキャン上で 1.80〜1.94 m（6 ft）。
- rt4090 の CPU での位置合わせ：1 プロセス 60〜85 ms/フレーム、24 本並列で 59 フレーム/s。

## 機械

- Studio の `~/mgp` は 2026-10-20 に丸ごと rt4090 の `/mnt/data/mgp_studio/` へ移した（Studio の空きが足りないため）。録画 `raw/`（AWS の
  一覧の中身のある 230 本が全部そろっている）、今の地図 `mapfinal`・`mapfixed`、`finals`、`rounds/*_loc`、台本。`make_mgp_race.py` の
  クリップと `round_mac.sh` の loc JSON はそこを読む（`MGP_HOST`・`MGP_DIR`、`LOC_HOST`・`LOC_DIR` で替えられる）。Studio 用の台本
  （`extract.py`、`localize.py` など）の中の `~/mgp` は書き換えていない。Studio で回すなら場所を戻すか引数で渡す。
  片付けで消したもの：60 fps の切り出し `all60`、各 Round の画像（録画から作り直せる）、フライスルーと地図の途中の版。
  手で付けた錨などの小物は `/mnt/data/mgpwork/studio_archive/`。
- **Mac・Studio から rt4090 への大きな転送は LAN のアドレス（10.0.0.64）で。** 名前の `rt4090` は Tailscale のアドレスに解決され、
  同じ LAN の中でも暗号化トンネルを通って毎秒 35 MB で頭打ちになる（LAN なら 134 MB、rt4090 は 2.5 GbE）。Mac の tar は `._*` を
  混ぜるので `COPYFILE_DISABLE=1` を付ける。
- rt4090（`/mnt/data/mgp`：chain・mapfix・gates・q44・gs_*、`/mnt/data/mgpwork`：穴埋め・夜の地図）：地図のつなぎ足し、位置合わせ、3DGS。
  2026-10-19 に Ubuntu を 2 TB の NVMe に移した。`/mnt/data` はいまルートの ext4 の普通のフォルダで、Linux の作業フォルダ（MGP・FDF・JDL・
  オフィス・spirula など）はここにある（パスは前と同じ）。Windows と共有の NTFS「Data」ドライブは `/mnt/ntfs-data`（fstab に無いので
  再起動後は `sudo mount -t ntfs3 -o uid=1000,gid=1000,iocharset=utf8 /dev/disk/by-label/Data /mnt/ntfs-data`。デバイスの番号は
  ディスクの入れ替えで変わる）。NTFS に残したもの（`Users`・`ZTMYSet_archive` など）には `/mnt/data/<名前>` からリンクがある。
  GPU は ZTMY-Sets のセッションとクロスセッションのメッセージで順番を決める。`pgrep -x spirula` が空なら使ってよい。
- `ssh host 'until ! pgrep -f "<名前>"'` は ssh の bash -c の行に当たって終わらない。`pgrep -f "[n]ame"` と書く。`pkill -f` も同じ理由で
  自分の bash を殺す。

## 残タスク

- 公開は 208 Round（予選 141、決勝 901〜905、Main の勝ち抜き A 911〜923・E 932〜943・D 953〜963・C 971〜983・B 991〜1003）。
  予選で残るのは：20〜22・50 は録画が空、29〜36・49・51・
  121〜136・142・152・156・168・171 は録画が無い、80 はラップが無い、178 は未確認。
- ラップが公式より少ない人が 798 人中 86 人（公式と 1 秒以内で全周が合うのは 700 人）。数え方は直した（下の「ラップの数え方」）。
  残りは軌跡のほうの穴で、抜けた通過 107 のうち 72 は通過の前後 0.5 秒に解けたフレームが無い（ゴール 47、途中 25）。
  29 は解けているのにゲートから離れた位置。位置合わせのやり直し（rt4090）でしか直らない。多いのは昼の 8 分割の予選。
- つなぎ足し：r1451 以降（14:51 → 13:39 → 12:43 → 12:25 → 12:01 → 11:39 → 11:18 → 10:56）。14:04 は 1,006 枚中 292 枚しか入らない。
  入った地図で Q44 を合わせ直して登録率を見る（走行中）。
- 7,523 枚の地図からの 3DGS（走行中、`/mnt/data/mgp/gs_1404`）。できたら `.sog` を差し替える。
- スタートの合図の時刻（帯のラップ表示の変化を密に OCR するか、Round ごとに手で）。`position`・`gap`・国旗の公式ソース。
- 決勝の線に残る長い直線：観測の無い数秒間を `poses_from_loc.py --smooth` がまっすぐ埋めたもの（A 決勝の KILLIANFPV など、直す前からある）。
- `tools/dvr/` の CPR 系 6 本（`cpr_track.py`・`cpr_rematch.py`・`cpr_match.py`・`gap_track.py`・`eval_align.py`・`pad_refine.py`）も
  MASt3R の遅い `fast_reciprocal_NNs` を使う。`cpr_gap_lib.reciprocal_nns` に替えれば 1 組あたり約 2 倍速い。
- レース 1 の 15 fps フレームを地図に足して 3DGS を厚くする（`loc_to_model.py` 未着手）。
