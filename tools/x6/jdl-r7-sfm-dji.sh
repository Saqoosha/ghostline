#!/bin/bash
# rt4090, JDL R7: the X6 walk (RTK-constrained, as jdl-r7-sfm.sh) plus DJI top-down frames in one SfM, so the scan also
# knows the field from above. dji/ is its own camera group (opencv) and sequence; it gets no position priors (its GPS is
# metres off): the walk's RTK sets the frame, the DJI frames register by their matches. Uses jdl-r7-sfm.sh's frames and masks.
#   writes scene/ws_rtk_dji; then: WS=ws_rtk_dji RUN=jdl_r7_dji NAME=JDL-2026-R7-dji bash jdl-r7-train.sh
set -euo pipefail
trap 'echo "== FAILED line $LINENO $(date)"' ERR
W=${W:-/mnt/data/JDL-2026-R7}; P=${P:-v009}; PY=$HOME/mastenv/bin/python
SPF=$HOME/src/spirula-rtk/build_vulkan/spirula
cd "$W/scene"
echo "== sfm rtk + dji $(date) ($(ls images/dji/*.jpg | wc -l) DJI frames)"
rm -rf ws_rtk_dji
"$SPF" sfm auto images -o ws_rtk_dji --data-type video --camera-model opencv-fisheye --camera-model dji=opencv \
  --masks masks2 --rig dual-fisheye=$P/cam0,$P/cam1 --sequence $P/cam0,$P/cam1 --sequence dji \
  --prefilter-neighbors 96 --metric-positions "$W/geo/positions.txt" \
  --metric-min-perp 0.02 --position-gate 2 --position-sigma 0.03 --audit > "$W/logs/sfm_rtk_dji.log" 2>&1 || echo "sfm exit $?"
grep -E "Models:|Model [0-9]+:|metric frame|RESULT|Reprojection|Total" "$W/logs/sfm_rtk_dji.log" | grep -v Registered | tail -8
ls ws_rtk_dji/sparse
echo "== dji registered: $(cd "$W" && $PY - <<'EOF'
import struct
n = 0; dji = 0
with open("scene/ws_rtk_dji/sparse/0/images.bin", "rb") as f:
    k, = struct.unpack("<Q", f.read(8))
    for _ in range(k):
        f.read(64); name = b""
        while (ch := f.read(1)) != b"\0": name += ch
        npt, = struct.unpack("<Q", f.read(8)); f.seek(24 * npt, 1); n += 1; dji += name.startswith(b"dji/")
print(f"{dji} of the DJI frames, {n} images in all")
EOF
)"
echo "== done $(date)"
