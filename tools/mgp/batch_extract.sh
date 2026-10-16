#!/bin/bash
# Extract per-pilot frames from several downloaded races into one work dir, waiting for each
# download to finish. Race names become r<HHMM> from the recording time.
# usage: batch_extract.sh <work_dir> <fps> <recording.mp4 basename>...
set -e
W="$1"; FPS="$2"; shift 2
cd "$(dirname "$0")"
for f in "$@"; do
  until [ -s "raw/$f" ]; do sleep 10; done
  name="r$(echo "$f" | sed -E 's/.*_([0-9]{2})-([0-9]{2})-[0-9]{2}\.mp4/\1\2/')"
  [ -d "$W/images/${name}_s0" ] || ls -d "$W/images/${name}_s"* >/dev/null 2>&1 && { echo "skip $name"; continue; }
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "raw/$f")
  RACE="$name" nice -n 10 env/bin/python extract.py "raw/$f" "$W" 0 "$dur" "$FPS" | sed "s/^/$name /"
done
