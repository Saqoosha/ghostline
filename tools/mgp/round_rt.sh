#!/bin/bash
# One qualifier round on rt4090: download the 8up recording, extract every live cell at FPS, localize each cell
# against the map. Output: /mnt/data/mgp/rounds/q<N>_loc/q<N>_s<slot>.json (localize.py format).
# usage: round_rt.sh <round> <url> <map_dir> [fps=30] [jobs=6]     (run under nice; CPU only)
set -e
N="$1"; URL="$2"; MAP="$3"; FPS="${4:-30}"; J="${5:-6}"
R=/mnt/data/mgp/rounds; mkdir -p "$R/raw"; cd "$R"
Q=$(printf "q%02d" "$N"); MP4="raw/$Q.mp4"
[ -s "$MP4" ] || { curl -sS --retry 3 -o "$MP4.part" "$URL" && mv "$MP4.part" "$MP4"; }
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$MP4")
[ -d "$Q/images" ] || RACE="$Q" nice -n 10 ~/mgp/env/bin/python ~/mgp/extract.py "$MP4" "$Q" 0 "$DUR" "$FPS" | tee "$Q.extract.log"
FOLDERS=$(cd "$Q/images" && ls -d ${Q}_s* | sed "s|^|$Q/images/|")
nice -n 10 ~/mgp/env/bin/python ~/mgp/localize.py "$MAP" "${Q}_loc" $FOLDERS -j "$J" 2>&1 | grep -E " ok, |Error|Traceback" | tee "$Q.loc.log"
