#!/bin/bash
# One qualifier round on the Mac Studio (rt4090 is off): extract every live cell at FPS from the downloaded recording
# and localize each cell against the map. Output: ~/mgp/rounds/q<N>_loc/q<N>_s<slot>.json (localize.py format).
# usage: round_studio.sh <round> <recording file in ~/mgp/raw> [map_dir=~/mgp/mapfinal] [fps=15] [jobs=8]
set -e
N="$1"; F="$2"; MAP="${3:-$HOME/mgp/mapfinal}"; FPS="${4:-15}"; J="${5:-8}"
cd ~/mgp; mkdir -p rounds; Q=$(printf "q%02d" "$N")
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "raw/$F")
[ -d "rounds/$Q/images" ] || RACE="$Q" nice -n 10 env/bin/python extract.py "raw/$F" "rounds/$Q" 0 "$DUR" "$FPS" > "rounds/$Q.extract.log"
FOLDERS=$(cd "rounds/$Q/images" && ls -d ${Q}_s* | sed "s|^|rounds/$Q/images/|")
nice -n 10 env/bin/python localize.py "$MAP" "rounds/${Q}_loc" $FOLDERS -j "$J" 2>&1 | grep -E " ok, |Error|Traceback" > "rounds/$Q.loc.log"
