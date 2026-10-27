# iPhone の AR：スキャンと飛行を部屋に重ねる

iPhone（ARKit）のカメラの絵に、同じ部屋の 3DGS スキャンと Whoop の飛行（軌跡・機体・名前）を重ね、DVR を隅に再生する。
真上から 3DGS で見る絵へ、なめらかに飛んで切り替えられる。試したのはオフィス 7F（`office-whoop-1`）と iPhone Air（LiDAR なし）。

- サーバー：`tools/dvr/rt_vps.py`（rt4090、mastenv）。位置合わせ（VPS）と、アプリに渡すデータ（点群・splat・隠すための円・飛行・DVR）
- アプリ：`ios/GhostlineAR/`（XcodeGen、SwiftUI ＋ ARKit ＋ SceneKit ＋ MetalSplatter 1.0.1、`sh.saqoo.ghostline-ar`、個人チーム VCFY2GFR89）

## 動かし方

```bash
# rt4090：常駐（ssh が切れても止まらないように systemd-run。PATH に mastenv が無いと gsplat の組み立てで ninja が見つからず落ちる）。
# 起動に約 40 秒、VPS-READY が出たら使える。認証が無いので tailnet / LAN の中だけで使う
sudo systemd-run --uid=saqoosha --unit=ghostline-vps --working-directory=/home/saqoosha/rt \
  -E HOME=/home/saqoosha -E PATH=/home/saqoosha/mastenv/bin:/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin \
  -E LOG=/home/saqoosha/rt/vps_log -E FLIGHT=/home/saqoosha/rt/ar/office-whoop-1 \
  /home/saqoosha/mastenv/bin/python -u rt_vps.py /mnt/c/Users/saqoosha/VDGS/dvr/rt/office/map_office2.npz /mnt/data/office-floor-video/office-7f-yup.ply
sudo systemctl stop ghostline-vps                       # 止める

# Mac：組んで入れる（Release で。Debug だと splat の並べ替えが遅い。初回は xcodebuild -downloadComponent MetalToolchain が要った）
cd ios/GhostlineAR && xcodegen generate
xcodebuild -project GhostlineAR.xcodeproj -scheme GhostlineAR -configuration Release -destination 'id=<UDID>' -derivedDataPath build -allowProvisioningUpdates build
xcrun devicectl device install app --device <UDID> build/Build/Products/Release-iphoneos/GhostlineAR.app
```

地図は rt4090 の Linux から Windows の C: を読み（`/mnt/c`）、ply は root の ext4 の `/mnt/data` に置く。iPhone からは Tailscale の `http://rt4090:8790`
（左上の操作のボタンで出る欄の、いちばん下の文字をタップで変えられる。ATS は `NSAllowsLocalNetworking` なので、1 語の名前か `.local` か IP で書く）。`FLIGHT` のフォルダには `poses60.json`（姿勢）・`dvr.mp4`・`index.json`（ビューアのもの。`room` を使う）・`lens.json`（その飛行の `dvr_pinhole.mp4.json`。`source_fisheye` を FPV に使う）を置く。

DVR は再生用に作り直す：録画の左右の黒（1280×720 の左 160 px から幅 960）を切って 4:3 に、離陸の直前から（オフィスは 19.3 s まで机の上なので 18.5 s から）。
姿勢も同じ秒数だけ引く。

```bash
ffmpeg -ss 18.5 -i src720.mp4 -vf crop=960:720:160:0,scale=480:360,setsar=1 -c:v libx264 -crf 23 -an -movflags +faststart dvr.mp4
```

## 位置合わせ（VPS）

アプリは ARKit の 1 フレーム（`capturedImage` を半分の 960×720 に、その内部パラメータ）を送り、サーバーがシーンの中のカメラ姿勢（OpenCV の軸）を返す。
アプリは同じフレームの ARKit 姿勢を手元に持っていて、`ARKit←シーン = ARKit のカメラ姿勢 × diag(1,-1,-1) × (シーンのカメラ姿勢)⁻¹`。
同じフレームの姿勢を使うので、通信の遅れは重なりに効かない（ARKit 姿勢は `ar=` でサーバーにも送るが、ログに残すだけ）。

- 1 回目は tracker の探し直しと同じ：DINOv2 で近いキーフレーム 8 枚 → LighterGlue → PnP（PoseLib）→ 見つけた姿勢で **iPhone の内部パラメータのまま** 3DGS を描いて照合、を 2 回。
  地図のキーフレームはドローンの 100° で描いてあり、iPhone は約 65° なので、描き直す 2 段目が効く
- 2 回目からはアプリが予測（いまの変換で見たカメラの位置）を送り、サーバーは探し直しを飛ばして描いて照合するだけ（`track`）。
  答えが予測から 0.5 m か 10° 以上離れたら採らず、探し直す（床や机の並びのような繰り返しに一致して、間違った変換で固まらないように）
- アプリは答えが返ったら次を送る（約 2.7 Hz、5 秒で諦める）。新しい変換へは 0.3 秒かけて寄せる。真上で止まっているあいだは送らない（移り変わりの間は送る）
- 変な入力は 400、サーバーの例外は 500（トレースバックは journal）。アプリは読み込みの失敗を画面の下に赤で出し続ける

**基準値**：DVR の 3502 フレーム（真値 `poses60.json`）で 6 mm（初回 507 ms、インライア 439）、予測を 15 cm ずらしても 9 mm に戻る。
iPhone で歩いて 108 秒：291 回すべて `track`、サーバー p50 159 ms、インライア p50 855、1 回の直し p50 1.3 cm・p90 3.0 cm・最大 9.5 cm。

## 決めたこと

- **ずれの原因は ARKit の漂いで、縮尺ではない。** 2 秒ごとに合わせると、歩いたときに 2 秒で p50 3.6 cm・p90 21 cm ずれた。
  スキャン ÷ ARKit の移動距離は 1.04〜1.05 に見えたが、直近の答えをまとめて縮尺ごと当てはめると次の答えの予測が悪くなった（1 個 3.6 cm → 20 個 8.6 cm）。
  変換が一定でないので、まとめずに続けて直す。1 回 ARKit が 54 cm 跳んだ
- **点群・軌跡は SceneKit の描画の中で Metal で描く**（`SCNNodeRendererDelegate`、`PointRenderer` / `PathRenderer`）。別の透明な view に描くと、カメラの絵から 1 フレーム遅れた
- **軌跡が物の後ろに隠れる**：スキャンの不透明な splat（不透明度 0.6 以上、半径 2σ ≤ 0.3 m、約 18.5 万）を、大きさに合わせた円として奥行きだけに描いてから軌跡を奥行きで比べる。
  LiDAR が無いので ARKit から奥行きは取れない。円は 15 cm 奥へずらす（床すれすれの線が床に埋もれないように）
- **軌跡は PC のページと同じ形**：各フレームに前後 ±0.8 s（PC の既定は 0.4、操作で変えられる）の重み付き 2 次式（biweight）、直近 3 s を幅 14 px の帯で、古い端に向けて細く（3 割）薄く。機体は DVR の時刻でフレーム間を補間
- **真上の絵は MetalSplatter**：カメラの絵 → 3DGS を 0.3 s で溶かす（そのあいだ仮想カメラは ARKit のカメラに張り付く）→ 2.0 s で上がる（位置 ease-in-out、向き slerp）。戻りは降りて ARKit のいまのカメラに着地してから溶かす。溶かすのと飛ぶのを同時にしない
- **真上の向きは部屋で決める**：`room` の直角の角（x0, z0）を左下、+x を画面の上、+z を右（オフィスは長い z 方向が横になる）。床全体を 1.15 倍で収める
- **FPV（ドローンの目）**：カメラの絵 → 3DGS（0.3 s）→ DVR の時刻のドローンのカメラへ 2.0 s（動く行き先を追う）→ 3DGS → DVR（0.3 s）。着いたら splat は止めて、
  魚眼のままの DVR（`dvr.mp4`）を全画面（高さに合わせ、左右は黒）。戻りは逆順（DVR → 3DGS 0.3 s → ARKit のいまのカメラへ 2.0 s → カメラの絵 0.3 s）。
  着く瞬間に 3DGS を DVR の絵に重ねるため、映像を歪みのない版にせず、**3DGS のほうを DVR の魚眼に曲げる**：
  splat と軌跡を広めのピンホールで画面外に描き（DVR の 4:3 の縁が入るだけ。広さは計算で決まり、横長の iPhone で約 1.3 倍）、OpenCV の fisheye（`k1〜k4`）を
  Newton 法で逆にたどって画面へ写す。飛ぶあいだに歪み 0→1・焦点距離 iPhone→DVR（DVR の高さを画面の高さに合わせた値）・左右の黒 0→1 を同時に変える。
  魚眼の四隅と、アップグレードのレンズで多項式が折り返す先（θ ≈ 1.34 より外、Newton 法が答えを持たない）は黒。
  ドローンの向きは `poses60.json` の四元数を ±0.15 s で平均、位置は軌跡と同じ当てはめ（±0.8 s）。全画面の DVR は右上と同じ AVPlayer を `AVPlayerLayer` にも映す（時刻が完全にそろう）
- **天井**：`room.y1 − margin`（4.3 m）より上の splat を別の chunk にして、仮想カメラがその高さより上のあいだ隠す（PC ビューアの `top` と同じ考え）
- **熱**：60 fps で数分で発熱して絞られた。描画・表示の更新・ARKit の映像を 30 fps に。真上に着いて 0.5 s 後の splat の絵を 1 枚とっておき、あとはそれに軌跡だけを描く

## ハマりどころ（実体験）

- **SceneKit の `.point` に透明度が効かない。** `SCNNode.opacity`・`material.transparency`・フラグメントのシェーダで色に掛けるのも、iPhone では不透明のまま（0 か 1）。自前の Metal で描いて解決
- **SceneKit の既定は逆向きの奥行き（`usesReverseZ`）で 0 に初期化。** renderer delegate に渡る投影は普通の向きなので、`lessEqual` で比べた線が全部捨てられた。`view.usesReverseZ = false`
- **SceneKit は encoder に裏面カリングを残している。** 太い線（三角形の帯）だけが消え、点は出ていた。描く前に `setCullMode(.none)`
- **MTKView は隠しても描き続ける。** 60 万 splat の並べ替えが裏で回って重かった。`isPaused` も切り替える
- **SwiftUI のボタンを `disabled(busy)` にすると、1 秒に 3 回の通信で点滅した。** 続けて送る間は常に押せないようにする
- Python の `http.server` は Range を返さないので、AVPlayer に直接は渡せない。mp4 を丸ごと受けてファイルから再生している

## 残っていること

- ライブ：tracker の答え（PUSH の SSE）と受信機の映像をその場で受ける（いまは録画の再生だけ）
- 真上の絵で、部屋の横の壁（窓の外）が端にかぶるなら `viewer/src/occlude.ts` と同じ判定を足す
- 真上の間も AR の画面（カメラの絵）は 30 fps で描き続けている。熱がきつければ止める
- Vision Pro・Quest 3 への移植（Vision Pro はカメラの絵に Enterprise API の申請が要る。Quest 3 は Passthrough Camera API で申請なし）
