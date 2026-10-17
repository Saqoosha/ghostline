#!/bin/bash
# rt4090: replay a day's recordings (rt_rec.py's 2x2 grids) through the live tracker, one recording per run, at real time
# over SRT on this box (as rt_camsweep.sh), with the field day's live settings (4 cells, lens auto, RENDER=1, BA; no RKF / KFOUT unless set). MAXFPS does not
# thin SRT: the recordings' 60 fps all arrive, numbered at 60.
# One run per recording: a crash (such as gsplat's SIGFPE, since fixed) loses one recording's answers, and the log says which and where.
#   rt_replay_day.sh <out dir> <map.npz> <scene.ply> <recording.mp4> ...     env PORT (9000): the SRT port, to run two at once
#   env PUSH: the answers as a live feed on that port (8765 is what the control page's 3D view /view/ reads; the audience
#   viewer's rehearsal runs PUSH=8766 PORT=9100). Any other rt_track.py env passes through (RKF=1 with a grown map)
#   writes <out dir>/<recording stem>/<cell>.jsonl (R2 F1 R4 R5), tracker.log and start (when the video went out), and one line per run to <out dir>/runs.txt
set -u
OUT=$1 MAP=$2 SCENE=$3; shift 3; cd ~/rt; export PATH=$HOME/mastenv/bin:/usr/local/cuda-12.9/bin:$PATH; mkdir -p "$OUT"
PORT=${PORT:-9000}; CAM=cams/hdzero-nano90-upgrade-lens.json+cams/hdzero-nano90-stock-lens.json
for f in "$@"; do
  stem=$(basename "$f" .mp4); d="$OUT/$stem"
  [ -f "$d/done" ] && continue
  [ -f "$f" ] || { echo "$stem missing" >> "$OUT/runs.txt"; continue; }
  mkdir -p "$d"; specs=()
  for c in "R2 0:0" "F1 960:0" "R4 0:540" "R5 960:540"; do set -- $c
    specs+=("$MAP,srt://127.0.0.1:$PORT?mode=listener&latency=80000|$2:960:540|$CAM,$d/$1"); done
  SIGNAL=1 GRID=1920x1080 GRID_FPS=30 MAXFPS=30 GLUE=eng_linux/glue_mix.engine XFEAT=eng_linux/xfeat_fp32.engine \
    RENDER=1 RCLIP=2 SCENE="$SCENE" BA=0.5 BA_ACC=30 BA_ALPHA=60 python -u rt_track.py "${specs[@]}" > "$d/tracker.log" 2>&1 &
  T=$!
  until grep -q listening "$d/tracker.log" 2>/dev/null; do sleep 2; kill -0 $T 2>/dev/null || break; done
  date +%s.%N > "$d/start"   # wall clock when the video starts going out: lines up a screen recording of the page with the video
  ffmpeg -nostdin -v error -re -i "$f" -an -c:v copy -f mpegts "srt://127.0.0.1:$PORT?pkt_size=1316&latency=80000"
  # the tracker notices the end of the stream and writes its answers (finish(), long for a long recording); a TERM in the
  # middle of that cut it short (exit 130) and can lose the cells written after. Only stop one that is still there after 5 min
  for i in $(seq 150); do kill -0 $T 2>/dev/null || break; sleep 2; done
  kill -TERM $T 2>/dev/null; wait $T; rc=$?
  echo "$stem exit $rc $(date +%T)" >> "$OUT/runs.txt"
  [ $rc -eq 0 ] && ls "$d"/*.jsonl > /dev/null 2>&1 && touch "$d/done"
done
echo "all done $(date +%T)" >> "$OUT/runs.txt"
