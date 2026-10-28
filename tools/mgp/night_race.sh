#!/bin/bash
# rt4090 (night/, ~/mastenv on PATH): a night race onto the day map, as the finals were. Frames: src/rounds/<id>/{full,masks}
# (main_night_race.sh). Writes race/<id>/gap/<feed>.json for round_mac.sh.
#   localize on the night map (night_localize.py, cameras: night_race_prep.py) -> day map (night_anchor.py, the D final's
#   anchors and the local field as for the finals) -> the folded part re-checked on the scan and tracked (night_refine.py
#   ONLY_BOX, night_keep.py, cpr_gap.py)
# usage: night_race.sh <id> <scene.ply in map coords>      e.g. night_race.sh a01 gs/t45.ply
set -euo pipefail
cd /mnt/data/mgpwork/night
id=$1; SC=$2; O=race/$id; mkdir -p "$O"
if [ -d "src/rounds/$id/full" ]; then
  for d in src/rounds/$id/full/*; do f=$(basename "$d"); ln -sfn "$PWD/$d" "sk/images/$f"; ln -sfn "$PWD/src/rounds/$id/masks/$f" "sk/masks/$f"; done
  FEEDS=$(ls "src/rounds/$id/full" | tr '\n' ' ')
else
  FEEDS=$(ls sk/images | grep "^${id}_s" | tr '\n' ' ')    # already linked (na11: frames in the map at every third)
fi
python -u night_race_prep.py sk/sfm/models_srf2 "$O" $FEEDS
# an interrupted run leaves its features half written (hloc reuses them and fails on a missing image): start loc/ afresh
[ -s "$O/loc/results.txt" ] || { rm -rf "$O/loc"; WIN=0.7 NRET=5 QCAM_FROM="$O/qcam" python -u night_localize.py sk/sfm sk/sfm/models_srf2 sk/images sk/masks "$O/loc" $FEEDS; }
cat sk/loc2/results_all.txt "$O/loc/results.txt" > "$O/res.txt"
python -c "import json, sys; a = json.load(open(sys.argv[1])); a.update(json.load(open(sys.argv[2]))); json.dump(a, open(sys.argv[3], 'w'))" \
  sk/loc2/inliers_all.json "$O/loc/inliers.json" "$O/inl.json"
FRAME=../top_frame.json FIT=../fit01.png.json COURSE='dayposes/*/*.json' ANCHOR_PREFIX=fd_ LOCAL=1 FOLD=-138,-104,-60,-22 FOLD_K=15 FOLD_MIN=4 \
  python night_anchor.py "$O/res.txt" "$O/inl.json" "$O/anchor" dayloc/rounds/fd_loc "$O/tmpl" -- $FEEDS
ONLY_BOX=-141,-101,-63,-19 python -u ../night_refine.py "$SC" ../top_frame.json ../fit01.png.json sk/sfm/models_srf2 "$O/anchor" sk/images sk/masks720 "$O/refine" $FEEDS
python -u ../night_keep.py "$O/refine" ../top_frame.json ../fit01.png.json "$O/keep" $FEEDS
JUMP_MPS=1000 ALL=1 MAXGAP=150 python -u ../cpr_gap.py "$SC" ../top_frame.json ../fit01.png.json "$O/keep" "$O/refine/frames" "$O/gap" $FEEDS
echo "$id done"
