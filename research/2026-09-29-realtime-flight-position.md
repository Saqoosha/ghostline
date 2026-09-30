# FPV レースドローンのリアルタイム 6DoF 位置推定と描画：3DGS スキャン上で HDZero ライブ映像を使う方法

## Executive summary

ライブ映像は地上の HDZero 受信機から取れます。HDZero Event VRX は 1 チャンネルごとに HDMI 出力を持つので、キャプチャカードで PC に入れられます。30〜60 Hz の地図ベース位置推定で実測の速度が出ているのは、XFeat と LighterGlue のような疎な特徴点照合と、ACE や GLACE のようなシーン座標回帰です。3DGS を描画して照合する今の ghostline の方式は、1 フレームに 0.1〜1 秒以上かかるので、ときどき掛ける補正に回すのが妥当です。A2RL の自律レースで実績のある構成は、単眼カメラの疎な絶対位置と高いレートの IMU を EKF で融合するものです。ただし有人 FPV 機から地上に下ろせる姿勢テレメトリはレートが低く、生のジャイロは下ろせません。そのため映像側の追跡が主役になります。38 m/s、500〜800 deg/s、強い動きぶれという条件でこれらの手法を試した資料は見つかりませんでした。これが最大の未知数です。

## Key Findings

**確度が高いもの（3 方向からの検証をすべて通った主張）**

- **地上でライブ映像を取れる。** HDZero Event VRX は 1 台に 4 チャンネルあり、各チャンネルが HDZero かアナログのどちらかを受信できます。出力はチャンネルごとに HDMI 1 系統とコンポジット 1 系統なので、地上の受信機からキャプチャカードで HDZero のライブ映像を取り込めます（[HDZero Event VRX introduction](https://docs.hd-zero.com/event-introduction)）。
- **Event VRX の構成と電源。** HDZero 受信機が 4 基（各 2 アンテナ）、アナログ受信機が 8 基（2 基ずつダイバーシティ組）です。4 チャンネルで SMA アンテナ端子 2 つを共用します。電源は 8〜20 V DC、消費 30 W です（[HDZero Event VRX introduction](https://docs.hd-zero.com/event-introduction)）。
- **XFeat は 30〜60 Hz に十分な速度が出る。** GPU で VGA 画像の疎な推論が単一バッチで 150 FPS を超え、RTX 4090 でのバッチ推論では約 1,400 FPS です。ノート PC の i5 CPU でも素の PyTorch で実時間に動きます（[verlab/accelerated_features](https://github.com/verlab/accelerated_features)）。
- **照合側も速い。** XFeat に同梱の LighterGlue は元の LightGlue より約 3 倍速いとされます。記述子は 64 次元で、疎と準密の両方の照合に対応します。著者によれば他の深層局所特徴より最大 5 倍速いということです（[verlab/accelerated_features](https://github.com/verlab/accelerated_features)）。
- **A2RL 2025 優勝の MonoRace は地図を使っていない。** センサーはローリングシャッターの単眼カメラと IMU だけで、状態推定はニューラルネットのゲート分割とドローンの動力学モデルを融合したものです。制御ネットはフライトコントローラ上で 500 Hz で回り、モーター指令を直接出しました。最高速度は約 100 km/h です。推定器はカメラの干渉と IMU の飽和を扱う必要がありました（[MonoRace, arXiv 2601.15222](https://arxiv.org/abs/2601.15222)）。
- **A2RL の別のファイナリストはカメラ 60 Hz と IMU 400 Hz を融合している。** 単眼 RGB カメラ 60 Hz と IMU 400 Hz（Pixhawk 6C Mini）を使います。VIO（VINS-Mono。OpenVINS も試験）を 100 Hz で出し、U-Net のゲート検出を 30 Hz で回し、融合した状態を 100 Hz で出力します。ゲート検出から得るのは位置とヨーだけで、ロールとピッチは IMU から取ります。ゲートの目印を VIO に融合すると、位置 RMSE は VIO 単独の 27 分の 1 になり、姿勢 RMSE は先行研究より 70% 下がりました（[arXiv 2602.01860](https://arxiv.org/html/2602.01860)）。
- **ただしその検証速度は FPV レースよりかなり遅い。** 同じ研究で確かめた速度は、実機実験で最大 10 m/s、A2RL で 5〜12.5 m/s、加速度は最大 7 g でした。38 m/s の FPV レースとは速度域が違います（[arXiv 2602.01860](https://arxiv.org/html/2602.01860)）。
- **ELRS テレメトリの帯域は狭い。** パケットレート 500 Hz、テレメトリ比 1:64 で毎秒約 7.8 パケット、帯域は 156 bps（バーストありで 234 bps）です。250 Hz、1:128 では毎秒約 2 パケット、約 39 bps です。FC テレメトリ（Advanced Telemetry の DATA）は MSP 転送と同じ帯域を使います。LINK 統計はそれとは別に約 512 ms ごと（毎秒約 2 回）に送られます（[ExpressLRS Telemetry Bandwidth](https://www.expresslrs.org/info/telem-bandwidth/)）。

**検証を経ていないもの（調査の要約から。数値は裏付けが弱い）**

- 下の Details に書いた受信・遅延・VO・Web 配信の数値のうち、上の出典リストにないものは、調査の途中で集めた要約から採っています。今回の検証は通していないので、採用前に一次資料で確かめる必要があります。

## Details

### 1. ライブ映像の取得

- **検証済み：** HDZero Event VRX は 4 チャンネルがそれぞれ独立した HDZero またはアナログの受信機です。チャンネルごとの HDMI 出力をキャプチャカードに入れれば、パイロットの映像を地上で取れます（[HDZero Event VRX introduction](https://docs.hd-zero.com/event-introduction)）。受信アンテナは 4 チャンネルで SMA 端子 2 つを共用する構成です（同出典）。
- **未検証：** HDZero はペアリングのない一方向の放送で、アナログと同じように同じチャンネルに合わせた受信機が何台でも受けられ、パイロットのリンクには影響しないとされます。VRX4 / BoxPro やゴーグルにも HDMI 出力があります。ただしゴーグルの HDMI は 1280x800（16:10）で出るという報告（GitHub issue #355）があり、一部のキャプチャカード（AverMedia LGX2）では信号なしと出るそうです。VRX 系は 1280x720 を出すとされます。
- **未検証：** WiFi の RTSP 配信は 2〜3 秒遅れるので使えません。HDZero のリンク自体の遅延は最初の画素まで約 3 ms、1 フレーム全体で約 14 ms とされ、ライブの遅延はほとんどがキャプチャカードの分（USB3 / PCIe でおおよそ 10〜50 ms）になる見込みです。ただしキャプチャカードの数値は実測ではなく、出典のない LLM の要約です。
- **推測：** 地上の受信機はゴーグル（4 アンテナ）よりアンテナが少ない（Event VRX は 1 受信機あたり 2 本）ので、受信が弱くなりマクロブロック崩れが増えると考えられます。受信機の置き場所とアンテナの選び方は実地で確かめる必要があります。

### 2. 30〜60 Hz の地図ベース位置推定

- **検証済み：** 実時間に届く数値が確認できたのは疎な特徴点です。XFeat は 4090 で抽出が 1 フレーム数 ms 以下に収まる速度で、LighterGlue は LightGlue の約 3 倍速です（[verlab/accelerated_features](https://github.com/verlab/accelerated_features)）。
- **構成案：** 3DGS から視点を密に描画し、XFeat の特徴と深度から 2D-3D の目印地図を事前に作ります。ライブではフレームごとに、予測した姿勢の近くのキーフレームと LighterGlue で照合し、PnP-RANSAC で姿勢を求めます。いまの MASt3R の密な照合（約 1 秒/フレーム）をフレームごとの追跡から外すのが中心です。
- **未検証：** SuperPoint と LightGlue は RTX 3080 で 1 ペア約 6.7 ms（1024 点。4096 点で約 20 ms）、TensorRT で最大 4 倍速くなるとされます。シーン座標回帰では、ACE が約 56 Hz（約 18 ms）、キーポイントで絞る ACE の派生が 90 Hz、GLACE が約 31 FPS です。屋外では GLACE のほうが正確で、Cambridge GreatCourt の中央誤差は 19 cm（ACE は 43 cm）と報告されています。
- **未検証：** 3DGS に特化した位置推定の多くは 1 フレーム 0.1〜1 秒以上かかります（GSFeatLoc 約 0.1 s、iComMa 1 s 超、6DGS は RTX 3090 で約 15 FPS）。例外は Splat-Nav（T-RO 2025）の Splat-Loc で、GSplat の地図に対してフレームごとに PnP 型の再帰更新を 1 回行い、実機飛行で約 25 Hz で追跡したとされます。
- **提案：** 速い追跡（XFeat / LighterGlue か ACE / GLACE）と、いまの描画して照合する方式（GS-CPR / GSFeatLoc 型）による低頻度の補正を組み合わせる 2 層構成です。**動きぶれの強い条件でこれらを試した資料は見つかっていません。**

### 3. 再位置推定の間をつなぐ視覚オドメトリ

- **未検証：** 学習ベースの VO が動きぶれと速い回転にいちばん強いとされます。速度は DROID-SLAM が EuRoC で約 20 FPS、DPVO / DPV-SLAM が約 50 FPS（VRAM は 3 分の 1）、MASt3R-SLAM が 4090 で約 15 FPS です。2026 年の UAV 研究では DPVO が 18.6 FPS、追跡成功率 86% で、劣化した条件では ORB-SLAM3 が破綻したと報告されています。
- **未検証：** 2026 年の Cioffi / Scaramuzza の研究（UZH-FPV と TartanAir）は、性能差の原因を再帰構造ではなく、学習した 2D の対応付けと不確かさの扱いにあるとしています。
- **未検証：** MASt3R-SLAM はまだ魚眼に対応しておらず、歪み補正が先に要ります。ORB-SLAM3 は魚眼と、保存した地図に対する位置推定専用モードを持ちます。ただし issue では、ぶれで追跡が切れることと、再位置推定が弱いことが報告されています。
- **検証済みの前例：** A2RL の推定器は、疎な視覚の絶対位置と高いレートの IMU を融合します。ロールとピッチは IMU から取ります（[arXiv 2602.01860](https://arxiv.org/html/2602.01860)、[MonoRace](https://arxiv.org/abs/2601.15222)）。ghostline でこれを真似るには、地図による補正の間の姿勢を何かでつなぐ必要があります。ところが下の 4 節のとおり、有人機からは高いレートの IMU を得にくいのが大きな違いです。

### 4. 機体側テレメトリ

- **検証済み：** ELRS のテレメトリ帯域は、パケットレートをテレメトリ比で割って決まります。500 Hz、1:64 で約 156〜234 bps、250 Hz、1:128 で約 39 bps です。FC のデータは MSP と帯域を分け合います（[ExpressLRS Telemetry Bandwidth](https://www.expresslrs.org/info/telem-bandwidth/)）。姿勢を高いレートで送る余裕は、標準の設定ではほとんどありません。
- **未検証：** Betaflight の CRSF の送信スケジューラは、ATTITUDE フレームを他のフレームと順番に回します。いちばん有望なのは Betaflight の MAVLink over ELRS で、Backpack の WiFi / UDP で PC に転送する経路です。姿勢のレートは `mavlink_extra1_rate`（既定 2 Hz）で決まり、100 Hz 出たという利用者の報告が 1 件あります。1〜8 kHz の生ジャイロはリンクに乗りません。
- **未検証：** HDZero の映像に乗るのは MSP DisplayPort の OSD 文字だけで、VTX は FC から VTX への送信しか受けません。数値のテレメトリは下りに流れないので、使うなら描画された OSD 文字を読み取るしかありません。ゴーグルの ESP32 の ELRS Backpack が扱うのはチャンネル、DVR、ヘッドトラッキングだけです。
- **未検証：** レース機サイズの M10 GNSS は既定 10 Hz、最大 25 Hz（u-blox PCN）で、CEP は約 1.5 m です。位置を緩く固定する役には立ちますが、38 m/s の飛行を単独で追うには粗く、遅すぎます。

### 5. 映像以外の手段と、プロのレースで使われているもの

- **検証済み：** 自律レースの MonoRace と A2RL のファイナリストは、どちらも機体に積んだセンサー（単眼カメラと IMU）だけで状態を推定します。外部の測位設備は使っていません（[MonoRace](https://arxiv.org/abs/2601.15222)、[arXiv 2602.01860](https://arxiv.org/html/2602.01860)）。
- **未検証：** 有人でも自律でも、外部設備でドローンの 6DoF 位置を連続して出しているリーグはありません。DRL はゲートのトランスポンダ、機体からのテレメトリ、AR グラフィックが中心で、「コース上のドローンのリアルタイム測位」は構想のままでした。DRL は 2026 年に清算されています。
- **未検証：** RotorHazard のスプリットゲートは、ゲートごとの通過時刻を Socket.IO / MQTT / RHAPI で出します。コースに沿った 1 次元の粗い制約として使えます。UWB（約 10 cm、TDoA で 50〜100 Hz）は、屋外でドローンから送信することに法規制があります。ELRS 経由の GNSS の遅延（100〜200 ms）は Google の要約に基づく数値で、確認されていません。地上の複数カメラによる三角測量は研究が多く、分散型の実時間多視点トラッカーも出ています。

### 6. ブラウザのビューアへの配信

- **未検証：** 4090 の機械から約 60 Hz で姿勢パケットを送るなら、LAN では素の WebSocket で足ります。ループバックと UAV の IoT 研究の実測では、WebSocket は WebRTC DataChannel と同等か速いという結果でした。DataChannel や WebTransport が効くのはパケットロスがあって TCP の head-of-line blocking を避けたいときです。
- **未検証：** 映像も一緒に流す場合、低遅延の経路は OBS の WHIP から WHEP プレイヤー（LAN で約 42〜150 ms、Cloudflare Stream などのクラウド中継で 180〜300 ms）と、WebTransport 上の WebCodecs（60 ms 未満）です。`requestVideoFrameCallback` は表示するフレームごとに rtpTimestamp と captureTime を返すので、姿勢と映像をフレーム単位で合わせられます。
- **未検証：** 揺らぎと遅延は、ゲームのネットコードで使うスナップショット補間（Gaffer、Valve、geckos.io）で隠します。約 2 パケットのバッファを持ち、位置は Hermite 補間、回転は slerp で補間し、必要なら速度から短く外挿します。PlayCanvas の race ページには、この補間層を足せば載せられる見込みです。

## Conflicts and open questions

- **速度域の差。** 検証済みの A2RL の結果は 5〜12.5 m/s（実機で最大 10 m/s）です（[arXiv 2602.01860](https://arxiv.org/html/2602.01860)）。MonoRace も約 100 km/h（約 28 m/s）です（[MonoRace](https://arxiv.org/abs/2601.15222)）。どちらも 38 m/s、500〜800 deg/s の有人 FPV より遅く、そのまま当てはまるかは分かりません。
- **IMU の有無。** A2RL の構成は 400 Hz 級の機上 IMU が前提です。ghostline は地上側で推定するので、ELRS の帯域（[ExpressLRS Telemetry Bandwidth](https://www.expresslrs.org/info/telem-bandwidth/)）の制約を受けます。MAVLink 経由の 100 Hz という報告は 1 件だけで、再現は確かめられていません。高いレートの姿勢が取れなければ、映像だけで回転を追う必要があり、難しさが大きく変わります。
- **動きぶれの下での性能。** XFeat、ACE / GLACE、Splat-Loc、DPVO のいずれも、強い動きぶれ、芝が大半の画面、HDZero のマクロブロック崩れという条件での性能は分かっていません。いまの DVR 映像を使ったオフライン評価（実時間ではなく処理速度とは切り離した精度の評価）で確かめる必要があります。
- **ゴーグル HDMI の解像度。** 1280x800 と 1280x720 の食い違いと、キャプチャカードとの相性は、issue の報告があるだけで検証していません。Event VRX を使えばこの問題は避けられる見込みです。
- **遅延の見積もり。** キャプチャカードの遅延（10〜50 ms）と GNSS の遅延（100〜200 ms）は出典の弱い数値です。200 ms 以内という目標は、キャプチャ、推定（10〜30 ms）、配信（LAN で数 ms）、補間バッファ（約 2 パケット、約 33 ms）を足して届きそうです。ただし実測はありません。
- **撤回された主張。** なし。

## Sources

- [MonoRace (arXiv 2601.15222)](https://arxiv.org/abs/2601.15222) — A2RL 2025 優勝機。単眼ローリングシャッターカメラと IMU、ゲート分割と動力学モデルの融合、FC 上で 500 Hz の制御、約 100 km/h
- [A2RL finalist vision-inertial state estimation (arXiv 2602.01860)](https://arxiv.org/html/2602.01860) — 60 Hz カメラと 400 Hz IMU、VIO 100 Hz とゲート検出 30 Hz の融合、RMSE の改善、検証した速度域
- [HDZero Event VRX introduction](https://docs.hd-zero.com/event-introduction) — 4 チャンネルのイベント用受信機。チャンネルごとの HDMI / コンポジット出力、アンテナ構成、電源
- [verlab/accelerated_features (XFeat)](https://github.com/verlab/accelerated_features) — XFeat と LighterGlue の実装と、GPU / CPU での速度の数値
- [ExpressLRS Telemetry Bandwidth](https://www.expresslrs.org/info/telem-bandwidth/) — パケットレートとテレメトリ比ごとの帯域の表、MSP との共用、LINK 統計の送信周期

## ghostline での進め方（調査を受けた提案）

**リアルタイムは「オフラインの精度を速くする」問題ではなく、別の設計になる。** いまの通しは 1 フレーム約 1 秒で、しかも
束調整が未来のフレームも使う。ライブでは使えるのは過去だけで、1 フレーム 16 ms（60 fps）か 33 ms（30 fps）に収める必要がある。
一方で、レースの地図に点を出す用途なら 1 m 程度の誤差は許せるので、精度の目標は下げられる。

構成案（2 層）：

| 層 | 中身 | 頻度 | 予算 |
|---|---|---|---|
| 追跡 | 前フレームの姿勢から予測 → 予測位置の近くの**事前に作ったキーフレーム**（3DGS から描いた画像 ＋ 深度 ＋ XFeat 特徴）と LighterGlue で照合 → PnP-RANSAC → 等速／ジャーク事前の小さな フィルタ（過去だけ） | 30〜60 Hz | 10〜30 ms（1 フレームの処理時間。60 Hz は処理を重ねて 16 ms ごとに出す前提） |
| 補正 | 今の CPR（その場で描画 ＋ MASt3R）を別スレッドで間引いて回し、追跡を引き戻す。見失ったら DINOv2 検索で再局在化 | 1〜5 Hz | 200 ms〜1 s |

- キーフレームは**過去の飛行の経路に沿って**描いておく（FDF の 6 本の `poses60_pad.json` がある）。レースの機体はほぼ同じ線を飛ぶので、
  地図の視点がそこに集まっていれば照合が効きやすい。経路そのものも「コースの線からの距離」として弱い事前に使える
- 回転 500〜800 °/s の区間では照合が落ちる（オフラインでも同じ）。そこは補間で持ちこたえ、描く側は点を少し遅らせる
  （補間バッファ）のが現実的。機体の IMU は ELRS で下ろせる帯域が数 Hz しかないので、当てにしない

**最初の一歩は機材なしでできる。** 手元の DVR（`build/dvr/*/dvr_pinhole.mp4`）を 60 fps で流して追跡層だけ回し、
オフラインの経路 `poses60_pad.json` を参照として（同じ映像から作った姿勢で、独立した真値ではない） (1) 1 フレームの処理時間、(2) 位置の誤差、(3) 見失う区間 を測る。ここで 30 Hz と 1〜2 m に届けば、
Event VRX ＋ キャプチャカードでのライブ試験と、race ページへの WebSocket 配信に進む。届かなければ、その区間の画を見て手を変える。

機材の側で確かめること：Event VRX（または VRX4 / BoxPro）の HDMI をキャプチャカードで取れるか、地上の受信で
マクロブロック崩れがどれだけ増えるか、キャプチャの遅延。
