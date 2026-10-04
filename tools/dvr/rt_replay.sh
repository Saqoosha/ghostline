#!/usr/bin/env bash
# Turn one rt_capture.sh recording into what the live page replays: <name>.mp4 (the stream as sent, remuxed) and
# <name>.json (the tracker's answers as an array). Works on a recording still being written.
#   tools/dvr/rt_replay.sh <rec dir> [name]      (default: the newest recording)   ->  live.html?replay=<name>
set -euo pipefail
REC="${1:?rec dir}"; NAME="${2:-$(ls -t "$REC"/*.mkv | head -1 | xargs basename | sed 's/\.mkv$//')}"
ffmpeg -v error -y -i "$REC/$NAME.mkv" -c copy -movflags +faststart "$REC/$NAME.mp4"
python3 - "$REC/$NAME" <<'PY'
import json, sys
rows = [json.loads(l[5:]) for l in open(sys.argv[1] + ".sse") if l.startswith("data:")]
# one tracker run only: the recording's rows restart at frame 0 when the tracker does
last = max((k for k in range(1, len(rows)) if rows[k]["i"] < rows[k - 1]["i"]), default=0)
json.dump(rows[last:], open(sys.argv[1] + ".json", "w")); print(len(rows) - last, "rows,", sum(r["how"] != "none" for r in rows[last:]), "solved")
PY
echo "$NAME"
