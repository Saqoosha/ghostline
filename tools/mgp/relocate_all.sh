#!/bin/bash
# On the studio, from ~/mgp: localize every round's frames near the final row again against the unfolded map and
# merge them into rounds/q<N>_locrow/ (relocate_row.py). A round with <out>/q<N>/done is skipped, so it can be rerun.
# usage: relocate_all.sh [map dir=mapfixed] [out=rowloc] [jobs=12] [round loc dirs... (default: all rounds/q*_loc)]
MAP="${1:-mapfixed}"; OUT="${2:-rowloc}"; J="${3:-12}"
DIRS=("${@:4}"); [ ${#DIRS[@]} -eq 0 ] && DIRS=(rounds/q*_loc)
for ld in "${DIRS[@]}"; do
  q=$(basename "$ld" _loc)
  [ -s "$OUT/$q/done" ] && continue
  env/bin/python relocate_row.py stage "$OUT" top_frame.json fit01.png.json "$ld"
  if [ ! -f "$OUT/$q/args.txt" ]; then echo "$q: nothing near the row"; mkdir -p "$OUT/$q"; echo none > "$OUT/$q/done"; continue; fi
  echo "== $q $(date +%T)"
  nice -n 5 env/bin/python localize.py "$MAP" "$OUT/$q/loc" "$OUT/$q"/images/* $(cat "$OUT/$q/args.txt") -j "$J" > "$OUT/$q/loc.log" 2>&1
  env/bin/python relocate_row.py merge "$OUT" "$ld" "rounds/${q}_locrow" top_frame.json fit01.png.json > "$OUT/$q/done.tmp" && mv "$OUT/$q/done.tmp" "$OUT/$q/done"
  cat "$OUT/$q/done"
done
