#!/bin/bash
# On the GPU machine, from the work dir that holds rounds/q<N>_locrow (relocate_row.py merge) and rounds/q<N>/images|masks
# (gap_frames.py's selection): cpr_gap.py for every round not done yet -> gaps/q<N>/*.json. WORKERS processes share the
# GPU, each taking every WORKERS-th round. A round with gaps/q<N>/done is skipped, so it can be rerun.
# usage: gap_all.sh <scene.ply in map coords> [workers=2]
PLY="$1"; WORKERS="${2:-2}"
export PATH=$HOME/mastenv/bin:/usr/local/cuda/bin:$PATH
run() {
  i=0
  for ld in rounds/q*_locrow; do
    i=$((i + 1)); [ $((i % WORKERS)) -eq "$1" ] || continue
    q=$(basename "$ld" _locrow)
    [ -f "gaps/$q/done" ] && continue
    [ -d "rounds/$q/images" ] || { echo "$q: no frames here"; continue; }
    echo "== $q $(date +%T)"
    mkdir -p "gaps/$q"
    if ~/mastenv/bin/python cpr_gap.py "$PLY" top_frame.json fit01.png.json "$ld" "rounds/$q" "gaps/$q" > "gaps/$q/log" 2>&1; then
      grep -E "^q[0-9]+_s" "gaps/$q/log"; touch "gaps/$q/done"
    else
      echo "$q: failed"; tail -3 "gaps/$q/log"
    fi
  done
}
for w in $(seq 0 $((WORKERS - 1))); do run "$w" > "gap_all_$w.log" 2>&1 & done
wait
