#!/bin/bash
# Finish a localized round on the Mac: fetch the per-cell poses from the studio, smooth them, name the cells by their lap
# times, build race.json and the clips (on the studio), add the round to the dev rounds.json.
# usage: round_mac.sh <round>      (from the repo root; publishing is a separate step: publish_mgp.sh)
# A Main's final (rounds 901-905, laps in data/mgp/livefpv_finals.json): ID=<its studio folder, e.g. fd> VF=<recording>
# LAYOUT=4up round_mac.sh 902
set -e
N="$1"; Q=$(printf "q%02d" "$N"); ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"
ID="${ID:-$Q}"                                        # the folder / cell name prefix on the studio
L="build/mgp/loc/$Q"; mkdir -p "$L"
# the cells' poses from the machine that localized them (LOC_HOST:LOC_DIR), unless already here
LOC_HOST="${LOC_HOST:-studio}"; LOC_DIR="${LOC_DIR:-mgp/rounds}"
ls "$L"/${ID}_s*.json >/dev/null 2>&1 || scp -q "$LOC_HOST:$LOC_DIR/${ID}_loc/*.json" "$L/"
[ -e "$L/top_frame.json" ] || scp -q "$LOC_HOST:mgp/top_frame.json" "$LOC_HOST:mgp/fit01.png.json" "$L/"
SPECS=$(cd "$L" && ls ${ID}_s*.json | sed -E "s|(${ID}_s([0-9]+))\.json|$L/\1.json=s\2:\2|")
uv run -q --with numpy --with scipy python tools/mgp/poses_from_loc.py "$L/top_frame.json" "$L/fit01.png.json" "build/mgp/poses/${Q}_slots" $SPECS --smooth 2>&1 | grep -v Warning
uv run -q --with numpy python tools/mgp/name_slots.py "$N" "build/mgp/poses/${Q}_slots" "build/mgp/poses/$Q" 60
# the recording the round was localized from: the studio queue says (round N <-> file), not the manifest's alias, which
# is wrong for some rounds (a re-run labelled with the previous race's number) and missing for files fetched later
[ -n "$VF" ] || VF=$(ssh -n "$LOC_HOST" "grep '^$N ' $LOC_DIR/queue.txt | head -1 | cut -d' ' -f2")
[[ "$VF" =~ ^[0-9_-]+\.mp4$ ]] && VFA=(--video-file "$VF") || VFA=()
# CLIPS=0: race.json only, the clips already cut stay (the clip window comes from the start gate, so new poses
# elsewhere on the course do not move it)
CA=(--clips); [ "${CLIPS:-1}" = 0 ] && CA=()
SA=(); [ -n "$SKY" ] && SA=(--sky "$SKY")                # a night round: SCENE=<night scan> SKY=<its sky in the data folder>
CROP=40; [ "$LAYOUT" = 4up ] && CROP=46               # the overlay bar over each feed (4-up: 44 px at 960 x 540)
LAYOUT="${LAYOUT:-8up}" uv run -q --with numpy python tools/mgp/make_mgp_race.py build --round "$N" --poses "build/mgp/poses/$Q" --out "build/mgp/$Q" "${VFA[@]}" \
  --fps 60 --margin 1.5 --min-gap 3 --ocr-dir /nonexistent --gates build/mgp/gates/gates_1404.json --crop-top "$CROP" \
  --scene "${SCENE:-MGP-Champs-2026-unfold}" "${CA[@]}" "${SA[@]}" 2>&1 | grep -E "^round|diff"
ln -sfn "../../../build/mgp/$Q" "viewer/public/mgp-dev/$Q"
uv run -q python - "$N" "$Q" <<'PY'
import json, sys
n, q = int(sys.argv[1]), sys.argv[2]
p = "viewer/public/mgp-dev/rounds.json"; r = json.load(open(p))
race = json.load(open(f"viewer/public/mgp-dev/{q}/race.json"))
laps = json.load(open("data/mgp/livefpv_laps.json")) + json.load(open("data/mgp/livefpv_finals.json"))
off = next(x for x in laps if x["round"] == n)
best = min(((t, p["pilot"]) for p in off["pilots"] for k, t in p["laps"] if k > 0 and t > 0), default=None)
e = {"round": n, "label": off.get("label") or f"Qualifier Round {n}", "completed": off["completed"], "data": q,
     "pilots": [p["name"] for p in race["pilots"]], **({"fastest": {"pilot": best[1], "lap": best[0]}} if best else {})}
r["rounds"] = sorted([x for x in r["rounds"] if x["round"] != n] + [e], key=lambda x: x["round"])
json.dump(r, open(p, "w"), indent=1); print("rounds.json:", [x["round"] for x in r["rounds"]])
PY
