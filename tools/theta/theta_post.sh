#!/bin/bash
# rt4090: trained splats -> the old scan's frame (align/fine.json, sim_align.py) -> the live tracker's keyframe map.
#   theta_post.sh <work dir> <name> [ckpt dir]   (default: the newest checkpoint of scene/out/model0)
#   writes ~/scenes/<name>-s<step>.ply and ~/rt/map_<name>-s<step>.npz
set -euo pipefail
trap 'echo "== FAILED line $LINENO $(date)"' ERR
W=${1:?work dir}; NAME=${2:?name}; cd "$W"
CK=${3:-$(ls -d scene/out/model0/step-*.ckpt | sort | tail -1)}
STEP=$(basename "$CK" .ckpt | sed 's/step-0*//')
OUT=$HOME/scenes/$NAME-s$STEP.ply
echo "== apply $(date +%T) $CK -> $OUT"
$HOME/m3env/bin/python fit_transform.py --src "$CK/splat.ply" --apply align/fine.json --out "$OUT"
echo "== map $(date +%T)"
cd $HOME/rt
PATH=$HOME/mastenv/bin:/usr/local/cuda-12.9/bin:$PATH python -u rt_map.py "$OUT" $HOME/fdf-r6b-d05/dvr_pinhole.mp4.json \
  map_$NAME-s$STEP.npz "$W/align/map_poses.json" $HOME/rt/scan_cameras.json
ls -l "$OUT" map_$NAME-s$STEP.npz
echo "== done $(date +%T)"
