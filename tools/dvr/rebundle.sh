#!/usr/bin/env bash
# Re-solve only the bundle adjustment of a DVR that dvr_pipeline.sh already tracked (the tracker's matches stay on
# win4090), with the current cpr_ba.py and SIG_ACC / SIG_ALPHA overrides; round 1's matches only,
# so it replaces a round-2 poses60.json, then place the pad again. Minutes, not the hour of tracking.
#   [SIG_ACC=.. SIG_ALPHA=..] tools/dvr/rebundle.sh <name>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; NAME="${1:?name}"
HOST=win4090; WIN="VDGS/dvr/$NAME"; WSL="/mnt/c/Users/saqoosha/$WIN"; OUT="$ROOT/build/dvr/$NAME"
scp -q "$ROOT/tools/dvr/cpr_ba.py" "$HOST:$WIN/"
ENVS="${SIG_ACC:+SIG_ACC=$SIG_ACC }${SIG_ALPHA:+SIG_ALPHA=$SIG_ALPHA }"
ssh "$HOST" "wsl.exe -d Ubuntu-24.04 -u saqoosha -- bash -lc 'cd $WSL && ${ENVS}CAM=dvr_pinhole.mp4.json ~/mastenv/bin/python cpr_ba.py track/track_init.json track/track.jsonl:track/dump poses60_ba.json > ba.log 2>&1'"
scp -q "$HOST:$WIN/poses60_ba.json" "$HOST:$WIN/ba.log" "$OUT/"
tail -2 "$OUT/ba.log"
uv run --quiet --with numpy --with scipy --with opencv-python-headless python "$ROOT/tools/dvr/takeoff.py" \
  "$OUT/dvr_pinhole.mp4" "$OUT/poses60_ba.json" "$OUT/poses60.json"
