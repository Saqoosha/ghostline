# 3DGS 地図に対する単眼リアルタイム位置推定の精度と速度の改善（FPV レース機の DVR 映像）

## 要点

今回の検証を通った主張で見るかぎり、ghostline の速度を確実に上げられる手段は、照合器を ONNX 経由で TensorRT（fp16）に載せることだけである。根拠は LightGlue を TensorRT に載せると torch.compile 比で最大約 4 倍速くなるという測定である。ただし LighterGlue と XFeat は LightGlue-ONNX の対応表に無いので、書き出しは自前で行う必要がある。

3DGS ネイティブの位置推定のうち、地図の Gaussian に特徴を埋め込む方式（STDLoc、GSplatLoc）は、検証済みの資料の範囲では FPS が確認できない。そのため毎フレームの追跡に使う手段ではなく、DINOv2 による再局在化と合成グリッド地図を置き換える候補として扱う。

ブラー対策、IMU を使わない時間フィルタ、Apple Silicon への載せ替えは、検証済みの主張が得られていない。下の記述は未検証の調査メモに基づく。

## 主な発見

- LightGlue-ONNX が書き出す LightGlue は、ONNX Runtime の CUDA / TensorRT / OpenVINO 実行プロバイダで動く。抽出から照合まで通しで動的バッチに対応し、複数ペアを 1 回の呼び出しで照合できる。[fabio-sim/LightGlue-ONNX](https://github.com/fabio-sim/LightGlue-ONNX)
- 速度が上がるのは TensorRT 実行プロバイダの fp16 であり、CUDA 実行プロバイダではない。
  - SuperPoint+LightGlue を TensorRT に載せると、torch.compile した PyTorch より最大約 4 倍速い（RTX 4080 Laptop、PyTorch 2.4.0、ORT 1.18.1、TensorRT 10.2.0、CUDA 12.1）。[Accelerating LightGlue Inference with ONNX Runtime and TensorRT](https://fabio-sim.github.io/blog/accelerating-lightglue-inference-onnx-runtime-tensorrt/)
  - CUDA 実行プロバイダは、キーポイントが少なければ PyTorch とほぼ同じ速さで、多いと遅くなる。
- TensorRT 経由の制約は次の 3 点である。[Accelerating LightGlue Inference with ONNX Runtime and TensorRT](https://fabio-sim.github.io/blog/accelerating-lightglue-inference-onnx-runtime-tensorrt/)
  - キーポイント数の上限が 3840。
  - バッチの形を揃えるため、抽出器を固定 top-K に変える。
  - 書き出したモデルからは adaptive depth/width（early exit と pruning）が外れる。
- ArgMax の TopK を工夫すると約 30% 速くなる。FlashAttention-2 を使う融合モデルは、系列が長いとき（キーポイントが多いとき）最大 80% 速いと報告されているが、レイテンシの実数と GPU 名は書かれていない。[fabio-sim/LightGlue-ONNX](https://github.com/fabio-sim/LightGlue-ONNX)
- FP8 量子化（TensorRT）は FP32 比で最大約 6 倍低レイテンシになった（512×512、3840 点）。[FP8 Quantized LightGlue with TensorRT](https://fabio-sim.github.io/blog/fp8-quantized-lightglue-tensorrt-nvidia-model-optimizer/)
  - キーポイントが少ないと伸びは小さく、1024×1024・512 点では約 1.76 倍だった。
  - 難しいペアでは割り当てスコアが下がり、信頼度の閾値で対応点の大半が落ちる。
- FP16 の TensorRT エンジンは、試した全構成で大きく速くなった（512/1024 px、512〜3840 点、TensorRT 10.9、Ada SM89）。[FP8 Quantized LightGlue with TensorRT](https://fabio-sim.github.io/blog/fp8-quantized-lightglue-tensorrt-nvidia-model-optimizer/)
- LightGlue-ONNX が対応する抽出器は SuperPoint、DISK、RaCo-ALIKED の 3 つである。XFeat と LighterGlue は対応表に無い。[fabio-sim/LightGlue-ONNX](https://github.com/fabio-sim/LightGlue-ONNX)
- XFeat の疎な推論は、ノート PC の i5 CPU でも VGA 画像をリアルタイムで処理する。[verlab/accelerated_features](https://github.com/verlab/accelerated_features)
- STDLoc は画像検索を使わず、Feature Gaussian Splatting の上で sparse-to-dense に位置を出す。コードと学習済みモデルは MIT ライセンスで公開されている。[zju3dv/STDLoc](https://github.com/zju3dv/STDLoc)
  - 精度は 7-Scenes で平均 0.67 cm / 0.22°、Cambridge で平均 8.7 cm / 0.13°。
  - README に FPS や GPU の記載は無い。
- GSplatLoc（IROS 2025）は、XFeat の 64 次元記述子を改造したラスタライザで 3DGS 地図に直接埋め込む。[be2rlab/gsplatloc](https://github.com/be2rlab/gsplatloc)
  - 姿勢精緻化は `loc_inference.py` にある。
  - 評価は 7Scenes と Cambridge で行われ、コードは Apache 2.0 で公開されている。

## 詳細

### 1. 3DGS ネイティブの追跡と精緻化

検証済みの資料のうち、速度に触れているのは STDLoc だけで、その README には FPS が無い。

以下は未検証の調査メモの数字である。
- 個別手法：STDLoc が RTX 4090 で約 152 ms/クエリ、GS-CPR が約 190 ms（うち MASt3R 113 ms、PnP 52 ms、3DGS の描画は 3.7〜12 ms）、GSFeatLoc が 90〜150 ms、6DGS が約 15 fps。
- 手法群の横並び比較：レンダリングした RGB-D に対する照合と PnP は約 0.1 s/枚、光度 refine は 1 枚に十数秒。

この数字が正しければ、公開されている 3DGS ネイティブ手法で 60 fps の追跡として測られたものは無く、現行の 16 ms/iter のほうが速い。

60 fps の追跡に使える部分として調査メモが挙げたものは、gsplat/SLAM 系の解析的な SE(3) 姿勢ヤコビアンである。これを PnP 解の後段に置き、1〜数ステップの Gauss-Newton による光度・特徴メトリック精緻化に使う。ただし、レイテンシと精度への効果は検証済みの主張になっていない。

### 2. 地図そのものに特徴を持たせる方式

STDLoc と GSplatLoc は、キーフレーム検索と 2D-2D 照合をやめて、地図の Gaussian に対して直接照合する設計である。GSplatLoc は XFeat の記述子を Gaussian に蒸留するので、ghostline の現行の特徴と系統が揃う。

以下は未検証の調査メモの内容である。
- 速度は STDLoc が約 3.9 fps、GSplatLoc が約 0.6 fps で、毎フレームの追跡には遅い。
- 追随研究として ULF-Loc（CVPR26）、SplitGS-Loc、GSFFs（CVPR25）、SplatLoc（TVCG25）がある。
- シーン座標回帰（ACE、GLACE、R-SCoRe/scrstudio）は数分で学習でき、屋外に強い。PoI は 3DGS の描画で学習できることを示している。合成グリッドで作る新コースの地図に使える可能性がある。

### 3. XFeat/LightGlue/LighterGlue の高速化

一次資料は fabio-sim のリポジトリとブログ記事で、検証済みの主張は「主な発見」に挙げた。ghostline に当てはめるときに関わる点は 3 つある。

- 固定 top-K：現行の 2048 点は TensorRT の上限 3840 に収まる。
- adaptive depth/width が外れる：書き出し後は全点が全層を通る。
- 精度劣化：FP8 では難しいペアの対応点が閾値で消える。

以下は未検証の調査メモの内容である。
- XFeat と LighterGlue の ONNX/TensorRT 実装として、verlab の PR #5、XFeatTensorRT、xfeat_lightglue_onnx がある。
- 照合は O(K²) で重くなる（Orin Nano で 512 点が 7.9 ms、1024 点が 21.6 ms）。
- GPU の RANSAC は kornia の 2026 年の PR 群と NVIDIA cuNLS がある。ただし数百点規模の PnP では、CPU の PoseLib のほうが速いことがある。
- 予測姿勢から地図点を投影して近傍だけ照合する方式は、SL-SLAM と ICMR 2026 の論文が近い。

### 4. モーションブラーとローリングシャッター

検証済みの主張は無い。未検証の調査メモでは、露光中の軌跡をモデル化する手法が挙がっている。

- MBA-SLAM：露光の始点と終点の 2 姿勢を SE(3) 上で線形補間する。コードあり。
- BAD-Gaussians：3 次 B-spline で軌跡を表す。
- Gaussian Splatting on the Move：ブラーとローリングシャッターを速度から同時に補償する。

既存地図に対する追跡に流用するなら、光度 refine の段で「ブラーさせた描画」と比べる形になる。その場合、1 クエリあたりの描画回数が増える。ローリングシャッターは R6P 系の最小解法と連続時間 BA が主流とされる。

### 5. IMU を使わない時間フィルタ

検証済みの主張は無い。未検証の調査メモでは次が挙がっている。

- A2RL 系：CTU のドリフトモデル、ゲートの PnP と単眼の構成、Robust Monocular AI。
- 2026 年の IMU-free scene-flow 論文：機体ダイナミクスと推力を事前分布にした EKF。

いずれも現行のスライディングウィンドウ BA（ジャーク・加速度の事前分布）を置き換える、または補う候補である。

### 6. Apple Silicon

検証済みの主張は無い。未検証の調査メモの内容は次のとおり。

- cvg/LightGlue は MPS で point pruning を無効にしている（`'mps': -1`）。FlashAttention も MPS では使えない。M1 Max で 10〜17 Hz に留まる理由の候補になる。
- CoreML/ANE に載せるには、N の固定、adaptive 機構の除去、fp16 化、gather/NMS/top-k のモデル外への移動が必要。
- EliFuzz/cv に XFeat+LighterGlue の CoreML 書き出しがある。
- ORT の CoreML 実行プロバイダは、未対応の演算を黙って CPU で実行する。
- MLX 版の LightGlue/XFeat は見つからなかった。

日本語の資料は、Zenn の PINTO による LightGlue の ONNX 化メモと、Fixstars の 3DGS 姿勢推定の高速化記事がある程度である。これも未検証で、URL は確認していない。

## 相反する見解と未解決の点

- **棄却した主張：** 「ONNX Runtime の CUDA 実行プロバイダで 2〜4 倍速くなる」は誤りである。速くなるのは TensorRT 実行プロバイダの fp16（上限 3840 点、RTX 4080 で測定）だけで、CUDA 実行プロバイダは compiled PyTorch と同程度か、それより遅い。[Accelerating LightGlue Inference with ONNX Runtime and TensorRT](https://fabio-sim.github.io/blog/accelerating-lightglue-inference-onnx-runtime-tensorrt/)
- **fp16 の精度：** ghostline では、LighterGlue を fp16（autocast `mp`）で動かすと解けるフレームが半分になり、速くもならなかった（プロジェクトの CLAUDE.md の記録）。一方、fabio-sim は TensorRT の FP16 で一貫して速くなったと報告している。ただしその報告には FP16 の照合精度の評価が無い。FP8 では難しいペアで対応点が消えることは確認されている。TensorRT の FP16 が XFeat+LighterGlue の対応点数と解けるフレーム率を保つかどうかは未確認である。
- **adaptive 機構を外した影響：** ghostline の照合 19 ms（8 ペア）に early exit がどれだけ効いているかは測られていない。外すと逆に遅くなる可能性がある。
- **地図に特徴を埋め込む方式の速度：** STDLoc の README に FPS は無い。152 ms や 3.9 fps といった値は二次的な数字で、未検証である。
- **ブラー対応の描画：** 1 クエリあたりの描画回数が増える分のコストと、60 fps の予算に収まるかどうかは不明である。
- **ドメイン差：** STDLoc と GSplatLoc の評価は 7-Scenes と Cambridge に限られる。高速で動くドローン、芝の反復テクスチャ、合成ビューでの性能は誰も測っていない。

## ghostline への適用候補

1. **XFeat+LighterGlue を ONNX に書き出し、TensorRT の FP16 で動かす（固定 2048 点、動的バッチ）**
   - 根拠：TensorRT の FP16 で最大約 4 倍速く、構成を問わず速くなった。動的バッチで複数ペアを 1 回の呼び出しで照合できる。[blog](https://fabio-sim.github.io/blog/accelerating-lightglue-inference-onnx-runtime-tensorrt/) / [FP8 blog](https://fabio-sim.github.io/blog/fp8-quantized-lightglue-tensorrt-nvidia-model-optimizer/) / [repo](https://github.com/fabio-sim/LightGlue-ONNX)
   - 期待する効果：4 人バッチの照合 19 ms が縮み、1 人あたり 25〜32 Hz を引き上げる。精度への影響は不明。
   - 先に測ること：同じ録画で、PyTorch fp32 と比べた 1 フレームあたりの inlier 数、解けるフレーム率、p50/p90 の誤差。
   - 注意：LighterGlue は対応表に無いので、書き出しは自前になる。
2. **FP8 は使わない（少なくとも追跡には）**
   - 根拠：難しいペアで対応点が閾値で消える。キーポイントが少ないと高速化は小さい。[FP8 blog](https://fabio-sim.github.io/blog/fp8-quantized-lightglue-tensorrt-nvidia-model-optimizer/)
   - 期待する効果：ブラーした高速旋回フレームでのロストを避けられる。
   - 先に測ること：導入する場合でも、ブラーの強い区間での解けるフレーム率。
3. **書き出しの最適化（TopK、融合 attention）を入れたうえでキーポイント数を見直す**
   - 根拠：TopK の工夫で約 30% 速くなる。融合 attention は系列が長いときほど効く。[repo](https://github.com/fabio-sim/LightGlue-ONNX)
   - 期待する効果：速度の改善。2048 点を減らす判断は、書き出した後の時間で行う。
   - 先に測ること：1024/2048/3072 点ごとの照合時間と p90 誤差。
4. **地図に XFeat 記述子を埋め込み（GSplatLoc 方式）、再局在化と新コースの地図に使う**
   - 根拠：64 次元の XFeat 記述子を 3DGS に埋め込めること、コードが公開されていること。[be2rlab/gsplatloc](https://github.com/be2rlab/gsplatloc)
   - 期待する効果：合成グリッド地図での劣化（p90 0.9〜1.3 m）とロストの改善。速度は未検証で、毎フレームには使わない。
   - 先に測ること：新コースの録画での、0.33 s ロストからの復帰時間と復帰後の誤差。DINOv2 の場合と比べる。
5. **STDLoc を DINOv2 の再局在化の代わりに試す**
   - 根拠：検索なしで高精度（Cambridge で 8.7 cm / 0.13°）、MIT ライセンス。[zju3dv/STDLoc](https://github.com/zju3dv/STDLoc)
   - 期待する効果：復帰後の姿勢の精度。FPS は README に無く、ロスト時だけ使う前提になる。
   - 先に測ること：RTX 4090 での 1 クエリのレイテンシと、芝のコースでの成功率。
6. **XFeat を CPU で動かし、GPU を照合に専念させる（主に M1 Max 向け）**
   - 根拠：XFeat の疎な推論は i5 CPU でも VGA でリアルタイムに動く。[verlab/accelerated_features](https://github.com/verlab/accelerated_features)
   - 期待する効果：MPS の 10〜17 Hz のうち、抽出の分を GPU から外せる。効果の大きさは未測定。
   - 先に測ること：M1 Max の CPU で 640×480・2048 点の抽出時間と、MPS との比較。
7. **（未検証の候補）PnP の後段に、解析ヤコビアンによる 1 ステップの精緻化を入れる。ブラー対応の描画、ダイナミクスを事前分布にした EKF も同じ扱い**
   - 根拠：検証済みの主張は無い。
   - 先に測ること：gsplat で 640×480 を 1 回描画して逆伝播する時間が、16 ms の予算に収まるか。

## 参考ソース

- [fabio-sim/LightGlue-ONNX](https://github.com/fabio-sim/LightGlue-ONNX) — LightGlue の ONNX/TensorRT 書き出し。動的バッチ、融合 attention、FP8 に対応。対応する抽出器の一覧もここにある
- [Accelerating LightGlue Inference with ONNX Runtime and TensorRT](https://fabio-sim.github.io/blog/accelerating-lightglue-inference-onnx-runtime-tensorrt/) — TensorRT の FP16 で約 4 倍。CUDA 実行プロバイダとの比較と、上限 3840 点などの制約
- [FP8 Quantized LightGlue with TensorRT and NVIDIA Model Optimizer](https://fabio-sim.github.io/blog/fp8-quantized-lightglue-tensorrt-nvidia-model-optimizer/) — FP8 と FP16 の速度、および FP8 での対応点の減少
- [zju3dv/STDLoc](https://github.com/zju3dv/STDLoc) — Feature Gaussian Splatting 上で検索なしに sparse-to-dense で位置を出す。7-Scenes と Cambridge の精度
- [be2rlab/gsplatloc](https://github.com/be2rlab/gsplatloc) — XFeat の記述子を 3DGS に埋め込む位置推定（IROS 2025）
- [verlab/accelerated_features](https://github.com/verlab/accelerated_features) — XFeat の公式実装。CPU でリアルタイムに動く
