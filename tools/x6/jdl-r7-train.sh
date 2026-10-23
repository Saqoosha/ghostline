#!/bin/bash
# rt4090, JDL R7: after jdl-r7-sfm.sh - 10M-splat training on the RTK SfM, then the scene in the y-up frame
# (east, up, south; mapposes_x6.py writes the rotation) and the live tracker's keyframe map from level views along the walk.
#   env W (work dir), NAME (the scene name), P (the images prefix, v009), WS (the SfM, ws_rtk), RUN (the training run, jdl_r7)
#   writes ~/scenes/<NAME>-s30000.ply and ~/rt/map_<NAME>-s30000.npz
set -euo pipefail
trap 'echo "== FAILED line $LINENO $(date)"' ERR
W=${W:-/mnt/data/JDL-2026-R7}; NAME=${NAME:-JDL-2026-R7}; P=${P:-v009}; WS=${WS:-ws_rtk}; RUN=${RUN:-jdl_r7}
SP=$HOME/spirula/spirula; PY=$HOME/mastenv/bin/python
cd "$W/scene"
echo "== train $(date)"
N=$(ls images/*/*.jpg images/*/*/*.jpg 2>/dev/null | wc -l)
"$SP" train 360-camera --data . --colmap-recon-dir $WS/sparse/0 --mask-dir masks3 \
  --output-dir-prefix out --output-dir-name $RUN \
  --cap-max 10000000 --num-iterations 30000 --sh-degree 3 \
  --max-batch-per-epoch $((N / 2)) --split-batch 0 \
  --distraction-robustness mild --floater-suppression mild \
  --disable-viewer 1 --keep-viewer-alive 0 --steps-per-save 10000 > "$W/logs/train-$RUN.log" 2>&1
tail -2 "$W/logs/train-$RUN.log"
CK=$(ls -d out/$RUN/step-*.ckpt | sort | tail -1); STEP=$(basename "$CK" .ckpt | sed 's/step-0*//')
OUT=$HOME/scenes/$NAME-s$STEP.ply
echo "== y up $(date +%T) $CK -> $OUT"
cd "$W"
$PY tools/mapposes_x6.py scene/$WS/sparse/0 geo/map_poses-$RUN.json geo/enu2yup.json
$PY /mnt/data/fdf-2026-10-17/fit_transform.py --src "scene/$CK/splat.ply" --apply geo/enu2yup.json --out "$OUT"
echo "== map $(date +%T)"
cd $HOME/rt
PATH=$HOME/mastenv/bin:/usr/local/cuda-12.9/bin:$PATH python -u rt_map.py "$OUT" $HOME/fdf-r6b-d05/dvr_pinhole.mp4.json \
  map_$NAME-s$STEP.npz "$W/geo/map_poses-$RUN.json" $HOME/rt/scan_cameras.json
ls -l "$OUT" map_$NAME-s$STEP.npz
echo "== done $(date +%T)"
