#!/usr/bin/env bash
# One HDZero feed end to end on a Vast instance (udgp_setup.sh done; /root/udgp holds tools/, the map, the scan, src/ the
# originals): pick the lens by tracking 60 s with each, cut the pinhole video from the original, reverse it, track both
# directions at once on this feed's GPU, join them. Writes feeds/<name>/<name>.jsonl and feeds/<name>/done.json.
#   CUDA_VISIBLE_DEVICES=<gpu> tools/udgp_feed.sh <name> <source file> <start s> <dur s> <slot>
set -euo pipefail
NAME=$1 SRC=/root/udgp/src/$2 START=$3 DUR=$4 SLOT=$5
R=/root/udgp; D=$R/feeds/$NAME; mkdir -p "$D"; cd "$D"
export PATH=/opt/env/bin:$PATH XFEAT_DIR=/root/xfeat LIVE=0 RENDER=1 RCLIP=2 SCENE=$R/scan_t1.ply OMP_NUM_THREADS=1 FRAME_JPEG=1
PY=/opt/env/bin/python; T=$R/tools
track() { ln -sf "$1.json" "$1.mp4.json"; $PY $T/rt_track.py "$R/map_udgp.npz,$1.mp4,$2" > "$2.log" 2>&1; }
solved() { $PY -c "import json,sys; print(sum('pos' in json.loads(l) for l in open(sys.argv[1])))" "$1"; }
# --- lens: 60 s from 40 s in (the recording starts 10 s before the race), tracked with each definition
best=""; bn=-1
for L in stock upgrade; do
  $PY $T/udgp_pinhole.py "$SRC" "$($PY -c "print($START + 40)")" 60 "$SLOT" "$R/cams/hdzero-nano90-$L-lens.json" "trial_$L" > /dev/null
  track "trial_$L" "trial_$L"; n=$(solved "trial_$L.jsonl"); echo "$NAME lens $L: $n solved of 60 s"
  if [ "$n" -gt "$bn" ]; then best=$L; bn=$n; fi
done
rm -f trial_*.mp4
# --- the whole recording, both ways
$PY $T/udgp_pinhole.py "$SRC" "$START" "$DUR" "$SLOT" "$R/cams/hdzero-nano90-$best-lens.json" fw > /dev/null
bash $T/udgp_reverse.sh fw rv > /dev/null
rm -f fw_t.jsonl rv_t.jsonl                                  # no answers left from an earlier attempt
track fw fw_t & a=$!; track rv rv_t & b=$!
fa=0; fb=0; wait $a || fa=$?; wait $b || fb=$?                # both waited for (a bare wait returns 0 whatever they did)
[ $fa = 0 ] && [ $fb = 0 ] || { echo "$NAME: tracker failed (fw $fa, rv $fb), see fw_t.log / rv_t.log" >&2; exit 1; }
$PY $T/udgp_merge_track.py fw_t.jsonl rv_t.jsonl "$NAME.jsonl" > merge.txt
rm -f fw.mp4 rv.mp4
$PY -c "import json,sys; json.dump(dict(name=sys.argv[1], lens=sys.argv[2], trial=int(sys.argv[3]), merge=open('merge.txt').read().strip()), open('done.json','w'))" "$NAME" "$best" "$bn"
echo "$NAME done: lens $best, $(cat merge.txt)"
