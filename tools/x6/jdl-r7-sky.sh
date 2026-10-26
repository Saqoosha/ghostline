#!/bin/bash
# rt4090, JDL R7: the scan without its sky, and the sky as a panorama (as FDF CUP R6: VDGS docs/sky.ja.md). The X6 frames get
# SAM sky masks; training masks out the sky as well as the people and the stick, so no large splats are parked above the field,
# and the same frames' sky goes onto one equirect sky.jpg in the scene's frame (x east, y up, z south) for the pages' skybox.
# The DJI top-down frames keep their masks: they see little sky, and SAM took the green nets on the grass for it.
# The live tracker keeps the scan with the sky (its map and matching were built on that one).
#   env W (work dir), RUN (the training run), TRACK (course line for clear_above_field.py); needs tools/sky_pano.py and
#   tools/sky_fill.py from VDGS and cut_sky_splats.py in $W/tools. Writes ~/scenes/JDL-2026-R7-nosky-s30000.ply, the pages' scan
#   $W/geo/JDL-2026-R7-nosky-cut.ply (tools/x6/make_sogs.sh makes the .sog files; a new scene name, or browsers keep the old scan)
#   and $W/geo/sky.jpg (the event folder's sky.jpg: publish-event.sh names it in index.json)
set -euo pipefail
trap 'echo "== FAILED line $LINENO $(date)"' ERR
W=${W:-/mnt/data/JDL-2026-R7}; RUN=${RUN:-jdl_r7_dji_nosky}; P=v009; WS=ws_rtk_dji; TRACK=${TRACK:-$HOME/rt/tracks/jdl-2026-r7-v0.json}
SP=$HOME/spirula/spirula; PY=$HOME/mastenv/bin/python; MODEL=/mnt/data/spirula/models/sam3-q4_0.ggml
cd "$W/scene"
echo "== sky masks $(date)"
for t in 0 1; do
  d=images/$P/cam$t; o=masks_sky/$P/cam$t
  [ "$(ls $o 2>/dev/null | wc -l)" = "$(ls $d | wc -l)" ] && continue
  rm -rf $o.raw; mkdir -p $o.raw $o
  # white = sky (sky_pano.py reads it so); the frames are written in read order as frame_<n>.png, put back to the stems
  "$SP" sam track --model $MODEL --frames $d --text "sky; cloud" --keep-prompted --detect-every 5 --out $o.raw 2>&1 | tail -1
  paste <(ls $o.raw | sort) <(ls $d | sort | sed 's/\.jpg$/.png/') | while read a b; do mv "$o.raw/$a" "$o/$b"; done; rmdir $o.raw
done
echo "== training masks: masks3 less the sky $(date)"
$PY - <<'EOF'
import glob, os, shutil, numpy as np
from PIL import Image
n = 0
for m in glob.glob("masks3/**/*.png", recursive=True):
    out = m.replace("masks3/", "masks_nosky/", 1); os.makedirs(os.path.dirname(out), exist_ok=True)
    sky = m.replace("masks3/", "masks_sky/", 1)
    if not os.path.exists(sky): shutil.copy(m, out); continue
    a = np.asarray(Image.open(m).convert("L")); s = np.asarray(Image.open(sky).convert("L").resize(a.shape[::-1]))
    Image.fromarray(np.where(s > 127, 0, a).astype(np.uint8)).save(out); n += 1
print(f"{n} frames with the sky masked")
EOF
echo "== train $(date)"
N=$(ls images/*/*.jpg images/*/*/*.jpg 2>/dev/null | wc -l)
"$SP" train 360-camera --data . --colmap-recon-dir $WS/sparse/0 --mask-dir masks_nosky \
  --output-dir-prefix out --output-dir-name $RUN \
  --cap-max 10000000 --num-iterations 30000 --sh-degree 3 \
  --max-batch-per-epoch $((N / 2)) --split-batch 0 \
  --distraction-robustness mild --floater-suppression mild \
  --disable-viewer 1 --keep-viewer-alive 0 --steps-per-save 10000 > "$W/logs/train-$RUN.log" 2>&1
tail -1 "$W/logs/train-$RUN.log"
CK=$(ls -d out/$RUN/step-*.ckpt | sort | tail -1)
cd "$W"
$PY /mnt/data/fdf-2026-10-17/fit_transform.py --src "scene/$CK/splat.ply" --apply geo/enu2yup.json --out geo/$RUN-yup.ply
$PY tools/clear_above_field.py geo/$RUN-yup.ply "$TRACK" $HOME/scenes/JDL-2026-R7-nosky-s30000.ply 5.0
echo "== the pages' scan: nothing 20 m up (no tall structure at the venue; 22k splats there, half over 2 m wide), then what sits on the sky $(date)"
# the tracker keeps the scan above; the pages get this one (JDL R7: 231k more splats cut, the haze and shards past the walk's reach)
$PY - "$HOME/scenes/JDL-2026-R7-nosky-s30000.ply" geo/$RUN-h20.ply <<'EOF2'
import sys
from plyfile import PlyData, PlyElement
v = PlyData.read(sys.argv[1])["vertex"].data; PlyData([PlyElement.describe(v[v["y"] < -2.0 + 20], "vertex")], text=False).write(sys.argv[2])
EOF2
$PY tools/cut_sky_splats.py geo/$RUN-h20.ply scene/$WS/sparse/0 scene/images scene/masks_sky geo/enu2yup.json "$TRACK" geo/JDL-2026-R7-nosky-cut.ply
echo "== sky panorama $(date)"
$PY tools/sky_pano.py scene/$WS/sparse/0 scene/images scene/masks_sky geo/sky.npy --frame geo/enu2yup.json --width 4096 --step 2
$PY tools/sky_fill.py geo/sky.npy geo/sky.jpg
ls -l $HOME/scenes/JDL-2026-R7-nosky-s30000.ply geo/sky.jpg
echo "== done $(date)"
