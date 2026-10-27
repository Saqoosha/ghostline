#!/bin/bash
# rt4090: a course from THETA V videos -> 3DGS (spirula). Same steps as the X5 scans of ~/repos/Personal/SkiPlace3DGS (tools/stage1, stage3).
#   theta_build.sh <work dir>   (THETA MP4s in <work dir>/raw; run under `sudo systemd-run --uid=...`, see docs/fdf-2026-10-17-field-test.ja.md)
# THETA V video is dual-fisheye side by side (3840x1920, two 1920 circles): split into one video per lens first,
# then extract both at the same fixed frame indices (--keep 0) so cam0/cam1 pair up as a rig.
set -euo pipefail
trap 'echo "== FAILED line $LINENO $(date)"' ERR
W=${1:?work dir}
SP=$HOME/spirula/spirula
MODEL=/mnt/data/spirula/models/sam3-q4_0.ggml
TEXT="person; leg; shoe"
cd "$W"; mkdir -p split scene logs

echo "== extract $(date)"
for f in raw/R*.MP4; do
  n=v$(basename "$f" .MP4 | rev | cut -c1-3 | rev)
  for t in 0 1; do
    s="split/$n-cam$t.mp4"; d="scene/images/$n/cam$t"; m="scene/masks/$n/cam$t"
    [ -f "$d/.done" ] && continue
    [ -f "$s" ] || { ffmpeg -nostdin -hide_banner -loglevel error -i "$f" -an -vf "crop=1920:1920:$((t * 1920)):0" \
      -c:v h264_nvenc -preset p5 -cq 16 -f mp4 -y "$s.tmp" && mv "$s.tmp" "$s"; }   # never keep half a split
    rm -rf "$d" "$m"
    echo "-- $n cam$t $(date +%T)"
    "$SP" sam extract "$s" -o "$d" --skip 8 --keep 0 \
      --model "$MODEL" --text "$TEXT" --mask-mode image --mask-out "$m" > "logs/extract-$n-cam$t.log" 2>&1
    "$SP" sam mask "$d" --out "$m" --shape "ellipse 0.5,0.5,0.48,0.48" > "logs/mask-$n-cam$t.log" 2>&1
    nj=$(find "$d" -name "*.jpg" | wc -l); [ "$nj" -gt 0 ] && [ "$nj" = "$(find "$m" -name "*.png" | wc -l)" ] || { echo "no frames, or masks do not match: $d"; exit 1; }
    echo "   $(ls "$d"/*.jpg | wc -l) frames"
    touch "$d/.done"
  done
done

echo "== sfm $(date)"
cd scene
ARGS=()
for n in $(ls images); do ARGS+=(--rig "dual-fisheye=$n/cam0,$n/cam1" --sequence "$n/cam0,$n/cam1"); done
if [ ! -f ws/sparse/0/images.bin ]; then
  rm -rf ws
  "$SP" sfm auto images -o ws --data-type video --camera-model opencv-fisheye \
    --masks masks "${ARGS[@]}" --prefilter-neighbors 96 --audit > "$W/logs/sfm.log" 2>&1
fi
grep -E "Models:|Model [0-9]+:|RESULT|Reprojection|Total" "$W/logs/sfm.log" | grep -v Registered | tail -30 || true
for m in $(ls ws/sparse); do cat "ws/sparse/$m/gauge.txt" || true; done

echo "== train $(date)"
for m in $(ls ws/sparse); do
  "$SP" train 360-camera --data . --colmap-recon-dir "ws/sparse/$m" --mask-dir masks \
    --output-dir-prefix out --output-dir-name "model$m" \
    --cap-max 3000000 --num-iterations 30000 --sh-degree 3 \
    --distraction-robustness mild --floater-suppression mild --keep-viewer-alive 0 \
    > "$W/logs/train_model$m.log" 2>&1
  tail -4 "$W/logs/train_model$m.log"
done
echo "== done $(date)"
