#!/usr/bin/env bash
# One HDZero DVR -> every frame's camera pose on a 3DGS scan, no marks, no COLMAP.
#   [T0=<s> DUR=<s>] tools/dvr/dvr_pipeline.sh <dvr.ts> <name> <scan_cameras.json> <scene.ply on win4090, /mnt/c/... path>
# T0/DUR cut the flight out of the recording (default: all of it; hdz_0067 is T0=80 DUR=98).
# FPS=<n> for a source that is not the goggle DVR's 60 (a race broadcast, 30).
# Mac: undistort -> win4090 (WSL, scheduled task): cpr_track.py (relocalize on renders at the scan cameras, then
# track with render + MASt3R + PnP) -> cpr_ba.py (bundle adjustment on the fixed map) -> back to build/dvr/<name>/.
# Output: build/dvr/<name>/poses60.json (the viewer's pose format, takeoff.py applied), poses60_ba.json (before it) and the logs. Needs ~/mastenv with gsplat.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TS="${1:?dvr .ts}"; NAME="${2:?name}"; SCAN="${3:?scan_cameras.json}"; PLY="${4:?scene .ply path on win4090}"
[[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "not a name: $NAME" >&2; exit 2; }
HOST=win4090; WIN="VDGS/dvr/$NAME"; WSL="/mnt/c/Users/saqoosha/$WIN"; OUT="$ROOT/build/dvr/$NAME"; TASK="vdgs-dvr-$NAME"
mkdir -p "$OUT"
if [ ! -s "$OUT/dvr_pinhole.mp4" ]; then
  echo "== undistort"; uv run --quiet --with numpy --with opencv-python-headless python "$ROOT/tools/dvr/undistort.py" "$TS" "$OUT/dvr_pinhole.mp4" "${T0:-0}" "${DUR:-100000}" 100
fi
pwsh_() { ssh "$HOST" 'pwsh -NoProfile -NonInteractive -Command -' 2>/dev/null | tr -d '\r' | { grep -v CLIXML || true; }; }
state=$(echo "(Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue).State" | pwsh_)
if [ "$state" = Running ]; then echo "== $TASK is already running on $HOST, waiting for it"; else
echo "== send"
ssh "$HOST" "mkdir -p $WIN && rm -f $WIN/done $WIN/failed"
scp -q "$OUT/dvr_pinhole.mp4" "$OUT/dvr_pinhole.mp4.json" "$SCAN" "$ROOT/tools/dvr/cpr_track.py" "$ROOT/tools/dvr/cpr_ba.py" "$HOST:$WIN/"
cat > "$OUT/run.sh" <<EOF
#!/bin/bash
export CUDA_HOME=/usr/local/cuda-12.9 PATH=/usr/local/cuda-12.9/bin:\$PATH
cd $WSL
# round 1 draws every splat (CULL=0); culling is refine_flight.sh's round 2 - the d07m recipe (docs/dvr-localization.ja.md)
{ CULL=0 ~/mastenv/bin/python cpr_track.py $PLY dvr_pinhole.mp4 $(basename "$SCAN") track > track.log 2>&1 &&
  CAM=dvr_pinhole.mp4.json ~/mastenv/bin/python cpr_ba.py track/track_init.json track/track.jsonl:track/dump poses60_ba.json > ba.log 2>&1 &&
  touch done; } || touch failed
EOF
scp -q "$OUT/run.sh" "$HOST:$WIN/"
echo "== run on $HOST (scheduled task $TASK)"
pwsh_ <<EOF
\$a = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument '-d Ubuntu-24.04 -u saqoosha -- bash -lc "bash $WSL/run.sh"'
\$p = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest
\$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
Unregister-ScheduledTask -TaskName $TASK -Confirm:\$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TASK -Action \$a -Principal \$p -Settings \$s | Out-Null
Start-ScheduledTask -TaskName $TASK
EOF
fi
# the task outlives this script: rerun it while the task runs and it only waits; after that it starts over
until ssh "$HOST" "test -f $WIN/done -o -f $WIN/failed"; do sleep 60; done
scp -q "$HOST:$WIN/track.log" "$HOST:$WIN/ba.log" "$OUT/" 2>/dev/null || true
ssh "$HOST" "test -f $WIN/done" || { echo "failed on $HOST - see $OUT/track.log and ba.log" >&2; exit 1; }
scp -q "$HOST:$WIN/poses60_ba.json" "$OUT/"
tail -2 "$OUT/ba.log"
# the pad: the tracker rarely locks before takeoff, so those frames come from the flight (takeoff.py)
uv run --quiet --with numpy --with scipy --with opencv-python-headless python "$ROOT/tools/dvr/takeoff.py" \
  "$OUT/dvr_pinhole.mp4" "$OUT/poses60_ba.json" "$OUT/poses60.json"
echo "$OUT/poses60.json"
