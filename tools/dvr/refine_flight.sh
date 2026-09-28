#!/bin/bash
# Overnight refinement of one DVR already tracked by dvr_pipeline.sh, on win4090 (WSL, mastenv). All steps local here:
#   1 cpr_ba.py (distance quotas) + takeoff.py -> poses60_r1.json, eval_align.py -> align_r1.json
#   2 cpr_rematch.py at r1 (round 2 matching, culled, on the DVR-coloured scene; both rounds are measured on it) + cpr_ba.py over both rounds + takeoff.py -> poses60_r2.json, align_r2.json
#   3 keep the round with fewer frames whose far points (>15 m) sit over 10 px off -> poses60.json, reason in choice.txt
# usage: refine_flight.sh <flight dir>     tools in /mnt/c/Users/saqoosha/VDGS/tools
set -uo pipefail
D="$1"; TL=/mnt/c/Users/saqoosha/VDGS/tools; PLY=/mnt/c/Users/saqoosha/VDGS/scenes/FDF-2026-R6b-spirula-web-dvr2.ply; PY=~/mastenv/bin/python
export CUDA_HOME=/usr/local/cuda-12.9 PATH=/usr/local/cuda-12.9/bin:$PATH CAM=dvr_pinhole.mp4.json
cd "$D" || exit 1; rm -f align_r1.json align_r2.json   # a failed eval must not leave the last run's to be judged
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a refine.log; }
pad() {   # takeoff.py when the clip starts on the pad; a clip that does not (a race cut) keeps the solve as it is
  if $PY $TL/takeoff.py dvr_pinhole.mp4 "$1" "$2" >> refine.log 2>&1; then log "takeoff.py applied -> $2"
  else cp "$1" "$2"; log "takeoff.py found no takeoff; $2 is the bundle adjustment unchanged"; fi; }
log "round 1: bundle adjustment"
$PY $TL/cpr_ba.py track/track_init.json track/track.jsonl:track/dump poses60_ba_r1.json > ba_r1.log 2>&1 || { log "cpr_ba r1 failed"; exit 1; }
pad poses60_ba_r1.json poses60_r1.json
STEP=10 $PY $TL/eval_align.py $PLY dvr_pinhole.mp4 poses60_r1.json align_r1.json > align_r1.log 2>&1; tail -5 align_r1.log | tee -a refine.log
log "round 2: rematch at r1"
$PY $TL/cpr_rematch.py $PLY dvr_pinhole.mp4 poses60_r1.json track/track.jsonl rematch > rematch.log 2>&1 || { log "rematch failed"; cp poses60_r1.json poses60.json; exit 1; }
$PY $TL/cpr_ba.py poses60_r1.json track/track.jsonl:track/dump rematch/rematch.jsonl:rematch/dump poses60_ba_r2.json > ba_r2.log 2>&1 || { log "cpr_ba r2 failed"; cp poses60_r1.json poses60.json; exit 1; }
pad poses60_ba_r2.json poses60_r2.json
STEP=10 $PY $TL/eval_align.py $PLY dvr_pinhole.mp4 poses60_r2.json align_r2.json > align_r2.log 2>&1; tail -5 align_r2.log | tee -a refine.log
$PY - <<'PY' | tee -a refine.log
import json, shutil
def far(p):
    r = json.load(open(p)); f = [max(x["bins"].get("15-40", [0, 0])[1], x["bins"].get("40-inf", [0, 0])[1]) for x in r]
    return sum(v > 10 for v in f), sum(v > 5 for v in f), len(f)
a, b = far("align_r1.json"), far("align_r2.json")
pick = "r2" if (b[0], b[1]) <= (a[0], a[1]) else "r1"
shutil.copy(f"poses60_{pick}.json", "poses60.json")
msg = f"far >10 px / >5 px of frames: r1 {a[0]}/{a[1]} of {a[2]}, r2 {b[0]}/{b[1]} of {b[2]} -> {pick}"
open("choice.txt", "w").write(msg + "\n"); print(msg)
PY
[ "${PIPESTATUS[0]}" = 0 ] || exit 1
touch refine.done
