#!/bin/bash
# rt4090, JDL R7 venue (Kaizuka), X6 walk 2026-10-23 16:40 JST with live RTK (base E870EE26):
# rig masks -> frames + SAM person masks -> chirp geotag -> static operator mask -> FIX positions -> RTK-constrained SfM (fork).
# Training is a separate step, after the SfM is checked. Recipe: X6RTK2/tools/run_x6b.sh + run_x6b_v2.sh (2026-10-22).
# env W (work dir), NAME (the .insv's stem, in raw/x6/), LOG (the RTK .csv, from raw/), ORIGIN (lat,lon,hEll): a second walk in its own work dir
set -euo pipefail
trap 'echo "== FAILED line $LINENO $(date)"' ERR
W=${W:-/mnt/data/JDL-2026-R7}
SP=$HOME/spirula/spirula
SPF=$HOME/src/spirula-rtk/build_vulkan/spirula
PY=$HOME/mastenv/bin/python
MODEL=/mnt/data/spirula/models/sam3-q4_0.ggml
NAME=${NAME:-VID_20261023_164044_00_009}; V=raw/x6/$NAME.insv; P=v${NAME##*_}
LOG=${LOG:-raw/rtk/beepsync/20261023_074029.csv}
cd "$W"; mkdir -p tools probe geo logs scene
cp -n /mnt/data/X6RTK2/tools/*.py /mnt/data/JDL-2026-R7/tools/masks_windowed.py /mnt/data/JDL-2026-R7/tools/mapposes_x6.py tools/ 2>/dev/null || true

echo "== rig masks $(date)"
for t in 0 1; do $PY tools/rig_mask.py "$V" $t 1920 probe/rig$t.png probe/rigprev$t.jpg; done

echo "== extract $(date)"
for t in 0 1; do
  d="scene/images/$P/cam$t"; m="scene/masks/$P/cam$t"
  [ -f "$d/.done" ] && continue
  rm -rf "$d" "$m"
  "$SP" sam extract "$V" --track $t -o "$d" --skip 12 --keep 0 --scale 0.5 \
    --model "$MODEL" --text "person; stroller" --mask-mode image --mask-out "$m" 2>&1 | grep -E "frames written|total|error" || true
  "$SP" sam mask "$d" --out "$m" --shape "ellipse 0.5,0.5,0.48,0.48" 2>&1 | tail -1
  [ "$(ls "$d"/*.jpg | wc -l)" = "$(ls "$m"/*.png | wc -l)" ] || { echo "mask count mismatch $d"; exit 1; }
  touch "$d/.done"
done

echo "== geotag $(date)"
$PY tools/geotag.py "$V" "$LOG" --mic-distance 0.08 --av-offset-ms 0 --out geo/live > logs/geotag.log 2>&1
grep -E "marker chirp|WARNING|frames,|first video frame" logs/geotag.log   # the marker line: the camera clock resolved, not a 5 s slot guess
(cd scene/images && ls $P/cam0/*.jpg $P/cam1/*.jpg) > geo/names.txt

echo "== masks2: rig circle, 15 px around each person, and where the operator always is $(date)"
P=$P $PY - <<'EOF'
import glob, os
import numpy as np
from PIL import Image
from scipy import ndimage
P = os.environ["P"]
for t in (0, 1):
    circle = np.array(Image.open(f"probe/rig{t}.png")) > 0
    files = sorted(glob.glob(f"scene/masks/{P}/cam{t}/*.png"))
    small = (np.array(Image.fromarray(circle.astype(np.uint8) * 255).resize((480, 480), Image.NEAREST)) > 0)
    freq = np.zeros((480, 480))
    for p in files: freq += np.array(Image.open(p).resize((480, 480), Image.NEAREST)) == 0
    freq = np.where(small, freq / len(files), 0)          # share of frames a pixel inside the rig circle is a person
    np.save(f"probe/personfreq{t}.npy", freq)
    static = ndimage.binary_dilation(freq > 0.1, iterations=6)
    static = np.array(Image.fromarray(static.astype(np.uint8) * 255).resize((1920, 1920), Image.NEAREST)) > 0
    os.makedirs(f"scene/masks2/{P}/cam{t}", exist_ok=True)
    for p in files:
        drop = np.array(Image.open(p)) == 0
        drop = ndimage.binary_dilation(drop & circle, iterations=15) | static | ~circle
        Image.fromarray(np.where(drop, 0, 255).astype(np.uint8)).save(p.replace("/masks/", "/masks2/"))
    print(t, f"{len(files)} frames, static {static.mean()*100:.1f}%")
EOF

echo "== masks3 for training: the operator counted over +-45 frames (the stick was held differently along a walk) $(date)"
$PY -u tools/masks_windowed.py "$W" $P masks3 > logs/masks3.log 2>&1 &   # CPU, beside the SfM on the GPU
M3=$!

echo "== positions $(date)"
# one origin for every walk of the venue (the first FIX of the 2026-10-23 16:40 walk): their scenes, maps and lines share a frame
$PY tools/metric_positions.py geo/live/$NAME.frames.csv geo/names.txt geo/pos_antenna.txt geo/enu_all.csv \
  --origin "${ORIGIN:-34.418287338,135.366007387,87.9907}"
awk '{printf "%s %.4f %.4f %.4f\n", $1, $2, $3, $4 - 0.1197}' geo/pos_antenna.txt > geo/positions.txt
wc -l geo/names.txt geo/positions.txt

echo "== sfm rtk $(date)"
cd scene; rm -rf ws_rtk
"$SPF" sfm auto images -o ws_rtk --data-type video --camera-model opencv-fisheye \
  --masks masks2 --rig dual-fisheye=$P/cam0,$P/cam1 --sequence $P/cam0,$P/cam1 \
  --prefilter-neighbors 96 --metric-positions "$W/geo/positions.txt" \
  --metric-min-perp 0.02 --position-gate 2 --position-sigma 0.03 --audit > "$W/logs/sfm_rtk.log" 2>&1
grep -E "Models:|Model [0-9]+:|metric frame|position factors|RESULT|Reprojection|Total" "$W/logs/sfm_rtk.log" | grep -v Registered | tail -10
ls ws_rtk/sparse
cd "$W"; $PY tools/sfm_check.py geo/enu_all.csv scene/ws_rtk/sparse/0 2>/dev/null | tail -8
wait $M3; cat logs/masks3.log
echo "== done $(date)"
