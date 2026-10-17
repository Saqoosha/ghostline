#!/bin/bash
# Keep collecting what the Mains (finals) need while nobody watches, on the studio (always on): every INT s
#  1. LiveFPV laps of each Main by event id, plus the bare results page (the newest event: catches the A Main, whose
#     id is not known yet), one file per event in ~/mgp/finals/ (livefpv_bracket.py never shrinks a file);
#  2. the bracket pages as HTML, for the record;
#  3. the YouTube streams (picture and sound, up to 1080p) once each has ended (was_live), with the info JSON (start time).
# The DVR recordings are watch_new.sh's job.
# usage: finals_collect.sh [interval s=900]       (from ~/mgp; log: finals/collect.log)
INT="${1:-900}"; D="$HOME/mgp/finals"; mkdir -p "$D/html" "$D/stream"
BASE="https://mgp.livefpv.com/results/"
EVENTS="521648 521649 521650"                         # C, D, B Main (A Main: through the bare page)
CHANNEL="https://www.youtube.com/channel/UCg1di6tix5Ivs-InWnVm9EQ"
EXTRA="CX5GvTMxfa8"                                   # "2026 MultiGP Champs Official Track"
while true; do
  for id in $EVENTS; do
    python3 -I "$HOME/mgp/livefpv_bracket.py" "${BASE}?p=view_event&id=$id" "$D" 2>&1 | tail -1
  done
  python3 -I "$HOME/mgp/livefpv_bracket.py" "$BASE" "$D" 2>&1 | tail -1
  curl -s -A "Mozilla/5.0" "$BASE" -o "$D/html/results_$(date +%Y%m%d-%H%M).html"
  # the streams are restarted under new ids during a Main (the D Main had three), so read the channel's list every pass
  yt-dlp --flat-playlist --playlist-end 25 --print "%(id)s	%(live_status)s	%(title)s" "$CHANNEL/streams" 2>/dev/null \
    | grep -E "2026 MultiGP +Championship" > "$D/stream/list.tsv"   # the A Main's title has two spaces there
  { cat "$D/stream/list.tsv"; for v in $EXTRA; do printf '%s\tnot_live\textra\n' "$v"; done; } | while IFS=$'\t' read -r v st title; do
    [[ "$v" =~ ^[A-Za-z0-9_-]{11}$ ]] || continue
    [ -s "$D/stream/$v.mp4" ] && continue
    echo "$(date +%T) stream $v: $st  $title"
    if [ "$st" = "was_live" ] || [ "$st" = "not_live" ]; then
      # the picture too (up to 1080p): the broadcast shows the same feeds as the DVR recordings, so the two can be lined
      # up by eye as well as by the start tone; the audio rides in the same file
      yt-dlp -q --no-update --write-info-json -f "bv*[height<=1080]+ba/b[height<=1080]" --merge-output-format mp4 \
        -o "$D/stream/$v.%(ext)s" "https://www.youtube.com/watch?v=$v" < /dev/null \
        && echo "$(date +%T) stream $v: saved"
    fi
  done
  sleep "$INT"
done
