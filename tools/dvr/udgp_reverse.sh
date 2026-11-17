#!/usr/bin/env bash
# The tracker's pinhole video played backwards, so rt_track.py can enter a hard stretch from its far side (offline only).
# ffmpeg's reverse filter holds the whole input in memory, so the video goes in 10 s pieces, each reversed, joined last-first.
# Frame i of <out>.mp4 is frame N-1-i of <in>.mp4. <out>.json is <in>.json.
#   tools/dvr/udgp_reverse.sh <in stem> <out stem>
set -euo pipefail
IN="$1"; OUT="$2"; T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
ffmpeg -nostdin -v error -i "$IN.mp4" -f segment -segment_time 10 -reset_timestamps 1 -c:v libx264 -preset fast -crf 8 -pix_fmt yuv444p "$T/s%04d.mp4"
# the pieces in parallel (env P, 8)
ls "$T"/s*.mp4 | xargs -P "${P:-8}" -I{} sh -c 'ffmpeg -nostdin -v error -i "$1" -vf reverse -c:v libx264 -preset fast -crf 8 -pix_fmt yuv444p "${1%.mp4}r.mp4"' _ {}
ls "$T"/s*r.mp4 | sort -r | sed "s/^/file '/; s/$/'/" > "$T/list.txt"
ffmpeg -nostdin -v error -y -f concat -safe 0 -i "$T/list.txt" -c copy "$OUT.mp4"
cp "$IN.json" "$OUT.json"
frames() { ffprobe -v error -count_packets -select_streams v:0 -show_entries stream=nb_read_packets -of csv=p=0 "$1"; }
n_out=$(frames "$OUT.mp4"); n_in=$(frames "$IN.mp4")
[ "$n_out" = "$n_in" ] || { echo "$OUT.mp4: $n_out frames, $IN.mp4 $n_in (the merge needs them equal)" >&2; exit 1; }
echo "$OUT.mp4 $n_out frames"
