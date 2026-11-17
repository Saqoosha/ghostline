#!/bin/bash
# UDGP 2026 R3 HQ scan on Mac Studio: all original footage -> SfM aligned to the current scan's frame -> 30M splats
# trained as 3 partitions and merged into hq30m.ply (10x the page's 3M scan) -> a streamed-SOG viewer in lod/site.
# For viewing only, not for the ghostline pages.
#   360 walk (.insv): every 5th frame (the current scan's stems, every 15th, are a subset), SAM person masks
#   DJI 0021 (inside the hall) / 0022 (outside, aerial): sharpest of every 10 frames, no masks (people kept)
# The new SfM is Sim3-aligned onto the current ws/sparse/0 through the shared 360 frames (udgp_hq_align.py), so it
# sits in the same z-up metric frame as the current scan.
# usage: udgp_hq_build.sh extract|sfm|align|split|train|merge|web   
set -euo pipefail
S=~/tools/spirula/spirula
W=~/work/udgp-2026-r3
H=$W/hq
PY=$W/env/bin/python
M=~/tools/spirula/models/sam3-q4_0.ggml
PARTS=${PARTS:-3}
CAP=${CAP:-11000000}          # per part; the merge keeps each part's own region, so 3 parts land near 30M
ITERS=${ITERS:-40000}
stage=${1:?stage}
cd $H
mkdir -p logs

if [[ $stage == extract ]]; then
  for k in 0 1; do
    $S sam extract $W/video/VID_20261115_113714_${k}0_006.insv -o images/cam$k --skip 5 --keep 0 --track 0 \
      --model $M --text "person" --mask-mode image --dilate-ratio 0.05 \
      --mask-out masks/cam$k --accept-license sam3=yes 2>&1 | grep -v VK_ | tee logs/extract_cam$k.log
  done
  # fisheye border; cam0's circle is not found automatically
  $S sam mask images/cam0 --out masks/cam0 --shape "ellipse 0.5,0.5,0.505,0.505" 2>&1 | tee -a logs/extract_cam0.log
  $S sam mask images/cam1 --out masks/cam1 2>&1 | tee -a logs/extract_cam1.log
  [[ -f logs/white.png ]] || ffmpeg -v error -y -f lavfi -i color=white:s=3840x2160 -frames:v 1 -pix_fmt gray logs/white.png
  for c in 21 22; do
    $S sam extract $W/video_dji/DJI_*_00${c}_D.MP4 -o images/dji$c --skip 10 --keep -1 2>&1 | grep -v VK_ | tee logs/extract_dji$c.log
    mkdir -p masks/dji$c
    for j in images/dji$c/*.jpg; do ln -f logs/white.png masks/dji$c/$(basename "$j" .jpg).png; done
  done
fi

if [[ $stage == sfm ]]; then
  # no --telemetry: it would map the DJI frame numbers onto the .insv's IMU; scale and up come from the alignment
  $S sfm auto images -o ws \
    --sequence cam0,cam1 --sequence dji21 --sequence dji22 \
    --data-type video --quality high --camera-mode folder \
    --camera-model cam0=opencv-fisheye --camera-model cam1=opencv-fisheye --camera-model opencv \
    --rig dual-fisheye=cam0,cam1 2>&1 | tee logs/sfm.log
fi

if [[ $stage == align ]]; then
  $PY $W/tools/udgp_hq_align.py ws/sparse/0 $W/ws/sparse/0 ds/sparse/0 2>&1 | tee logs/align.log
  ln -sfn ../images ds/images
  ln -sfn ../masks ds/masks
fi

if [[ $stage == split ]]; then
  $S partition split ds --parts $PARTS 2>&1 | tee logs/split.log
fi

if [[ $stage == train ]]; then
  for k in $(seq 0 $((PARTS - 1))); do
    [[ -f ds/outputs/p$k/splat.ply ]] && continue
    $S train 360-camera --data ds --colmap-recon-dir sparse/0 --mask-dir masks \
      --partition ds/partition.json --partition-part $k \
      --output-dir-prefix ds/outputs --output-dir-name p$k \
      --cap-max $CAP --num-iterations $ITERS --sh-degree 3 --floater-suppression mild \
      2>&1 | grep -v VK_ | tee logs/train_p$k.log
  done
fi

if [[ $stage == merge ]]; then
  $S partition merge ds/partition.json --runs ds/outputs -o hq30m.ply 2>&1 | tee logs/merge.log
fi

if [[ $stage == web ]]; then
  # streamed-SOG viewer (PlayCanvas LOD, as WovenCity-3DGS): 10M chunks, levels 0..5 (30M down to ~1M)
  $W/tools/udgp_hq_lod.sh hq30m.ply lod 10000000 5 3 2>&1 | tee logs/web.log
fi
