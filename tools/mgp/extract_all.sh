#!/bin/bash
# Extract every downloaded round at FPS into one work dir, folders q<round>_s<slot>, several races at a time.
# The round number comes from aliases.json ("Bracket · Race N"); recordings without one are skipped.
# usage: extract_all.sh <work_dir> <fps> [parallel=6]     (run from ~/mgp on the studio: raw/, aliases.json, env/)
set -e
W="$1"; FPS="$2"; P="${3:-6}"
python3 -c '
import json, sys, re
a = json.load(open("aliases.json"))
for f, alias in sorted(a.items()):
    m = re.search(r"Race (\d+)", alias)
    if m: print(f, "q%02d" % int(m.group(1)))
' > "$W.list"
one() {
  f="$1"; name="$2"; W="$3"; FPS="$4"
  ls -d "$W/images/${name}_s"* >/dev/null 2>&1 && { echo "skip $name"; return; }
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "raw/$f")
  RACE="$name" nice -n 10 env/bin/python extract.py "raw/$f" "$W" 0 "$dur" "$FPS" | sed "s/^/$name /"
}
export -f one
mkdir -p "$W"
xargs -P "$P" -L 1 bash -c 'one "$0" "$1" "$2" "$3"' < <(awk -v w="$W" -v fps="$FPS" '{print $1, $2, w, fps}' "$W.list")
