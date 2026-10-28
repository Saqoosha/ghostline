#!/bin/bash
# Work through queued rounds on rt4090, one at a time: each line of /mnt/data/mgp/rounds/queue.txt is "<round> <url>";
# a round is done when rounds/q<N>.loc.log has its result lines. New lines may be appended while it runs.
# usage: round_queue.sh <map_dir> [fps=30] [jobs=6]
MAP="$1"; FPS="${2:-30}"; J="${3:-6}"; R=/mnt/data/mgp/rounds; mkdir -p "$R"; touch "$R/queue.txt"
while true; do
  next=""
  while read -r n url; do
    [ -z "$n" ] && continue
    q=$(printf "q%02d" "$n")
    if ! grep -q " ok, " "$R/$q.loc.log" 2>/dev/null; then next="$n $url"; break; fi
  done < "$R/queue.txt"
  if [ -z "$next" ]; then sleep 60; continue; fi
  set -- $next
  echo "== round $1 $(date +%T)"
  ~/mgp/round_rt.sh "$1" "$2" "$MAP" "$FPS" "$J" > "$R/q$(printf %02d "$1").out" 2>&1 || echo "round $1 failed"
done
