#!/bin/bash
# Work through queued rounds on the studio, one at a time: each line of ~/mgp/rounds/queue.txt is "<round> <recording file in ~/mgp/raw>";
# a round is done when rounds/q<N>.loc.log has its result lines. New lines may be appended while it runs.
# A round that ends without them is listed in rounds/failed.txt and skipped from then on (its error stays in rounds/q<N>.out),
# so one bad recording does not hold up the rest (Rounds 20-22: only the overlay was recorded, every feed is black).
# Delete its line there to try it again.
# usage: round_queue_studio.sh <map_dir> [fps=15] [jobs=8]
MAP="$1"; FPS="${2:-15}"; J="${3:-8}"; R=$HOME/mgp/rounds; mkdir -p "$R"; touch "$R/queue.txt" "$R/failed.txt"
while true; do
  next=""
  while read -r n url; do
    [ -z "$n" ] && continue
    grep -qx "$n" "$R/failed.txt" && continue
    q=$(printf "q%02d" "$n")
    if ! grep -q " ok, " "$R/$q.loc.log" 2>/dev/null; then next="$n $url"; break; fi
  done < "$R/queue.txt"
  if [ -z "$next" ]; then sleep 60; continue; fi
  set -- $next
  echo "== round $1 $(date +%T)"
  ~/mgp/round_studio.sh "$1" "$2" "$MAP" "$FPS" "$J" > "$R/$q.out" 2>&1
  grep -q " ok, " "$R/$q.loc.log" 2>/dev/null || { echo "round $1 failed"; echo "$1" >> "$R/failed.txt"; }
done
