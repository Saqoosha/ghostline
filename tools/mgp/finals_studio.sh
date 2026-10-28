#!/bin/bash
# The Mains' finals on the studio, from ~/mgp: each 4-up recording cut into its four feeds (extract.py LAYOUT=4up) and
# localized against the map -> rounds/<id>/images|masks, rounds/<id>_loc/<id>_s<slot>.json, rounds/<id>.loc.log.
# usage: finals_studio.sh <map dir> <id>=<recording in raw/> ...      e.g. mapfixed fd=2026-10-17_12-30-59.mp4
MAP="$1"; shift
for spec in "$@"; do
  id="${spec%%=*}"; f="${spec#*=}"
  if [ ! -d "rounds/$id/images" ]; then
    d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "raw/$f")
    RACE="$id" LAYOUT=4up nice -n 5 env/bin/python extract.py "raw/$f" "rounds/$id" 0 "$d" 15 > "rounds/$id.extract.log" 2>&1
  fi
  nice -n 5 env/bin/python localize.py "$MAP" "rounds/${id}_loc" $(ls -d "rounds/$id/images/${id}"_s*) -j 4 2>&1 \
    | grep -E " ok, |Error|Traceback" > "rounds/$id.loc.log"
  echo "$id ($f): $(cut -c1-60 "rounds/$id.loc.log" | tr '\n' ' ')"
done
