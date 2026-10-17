#!/bin/bash
# Finish a localized round on the Mac: fetch the per-cell poses from rt4090, smooth them, name the cells by their lap
# times, build race.json and the clips (on the studio), add the round to the dev rounds.json.
# usage: round_mac.sh <round>      (from the repo root; publishing is a separate step: publish_mgp.sh)
set -e
N="$1"; Q=$(printf "q%02d" "$N"); ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"
L="build/mgp/loc/$Q"; mkdir -p "$L"
# the cells' poses from the machine that localized them (LOC_HOST:LOC_DIR), unless already here
LOC_HOST="${LOC_HOST:-studio}"; LOC_DIR="${LOC_DIR:-mgp/rounds}"
ls "$L"/${Q}_s*.json >/dev/null 2>&1 || scp -q "$LOC_HOST:$LOC_DIR/${Q}_loc/*.json" "$L/"
[ -e "$L/top_frame.json" ] || scp -q "$LOC_HOST:mgp/top_frame.json" "$LOC_HOST:mgp/fit01.png.json" "$L/"
SPECS=$(cd "$L" && ls ${Q}_s*.json | sed -E "s|(${Q}_s([0-9]+))\.json|$L/\1.json=s\2:\2|")
uv run -q --with numpy --with scipy python tools/mgp/poses_from_loc.py "$L/top_frame.json" "$L/fit01.png.json" "build/mgp/poses/${Q}_slots" $SPECS --smooth 2>&1 | grep -v Warning
uv run -q --with numpy python tools/mgp/name_slots.py "$N" "build/mgp/poses/${Q}_slots" "build/mgp/poses/$Q" 60
uv run -q --with numpy python tools/mgp/make_mgp_race.py build --round "$N" --poses "build/mgp/poses/$Q" --out "build/mgp/$Q" \
  --fps 60 --margin 1.5 --min-gap 3 --ocr-dir /nonexistent --gates build/mgp/gates/gates_1404.json --crop-top 40 --clips 2>&1 | grep -E "^round|diff"
ln -sfn "../../../build/mgp/$Q" "viewer/public/mgp-dev/$Q"
uv run -q python - "$N" "$Q" <<'PY'
import json, sys
n, q = int(sys.argv[1]), sys.argv[2]
p = "viewer/public/mgp-dev/rounds.json"; r = json.load(open(p))
race = json.load(open(f"viewer/public/mgp-dev/{q}/race.json"))
laps = json.load(open("data/mgp/livefpv_laps.json")); off = next(x for x in laps if x["round"] == n)
best = min(((t, p["pilot"]) for p in off["pilots"] for k, t in p["laps"] if k > 0 and t > 0), default=None)
e = {"round": n, "label": f"Qualifier Round {n}", "completed": off["completed"], "data": q,
     "pilots": [p["name"] for p in race["pilots"]], **({"fastest": {"pilot": best[1], "lap": best[0]}} if best else {})}
r["rounds"] = sorted([x for x in r["rounds"] if x["round"] != n] + [e], key=lambda x: x["round"])
json.dump(r, open(p, "w"), indent=1); print("rounds.json:", [x["round"] for x in r["rounds"]])
PY
