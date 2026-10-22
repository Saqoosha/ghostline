"""Name a round's last unnamed cell by elimination: when name_slots.py left exactly one cell with a path unnamed and
exactly one of the round's pilots unplaced (no laps, or one lap that fits several pilots), they are each other. The
4-up name bars said the same each time this was done by hand (B final SHARK_FPV, A final MINJAE_, A Main races).
More than one of each is left alone and reported.
usage: name_by_elimination.py <round> ...   (from the repo root; writes build/mgp/poses/q<N>/<PILOT>.json and prints the
rounds to rebuild with make_mgp_race.py)"""
import glob, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
laps = json.load(open(f"{ROOT}/data/mgp/livefpv_laps.json")) + json.load(open(f"{ROOT}/data/mgp/livefpv_finals.json"))
for n in map(int, sys.argv[1:]):
    off = next((x for x in laps if x["round"] == n), None)
    if off is None: print(n, "no official entry"); continue
    q = f"{ROOT}/build/mgp/poses/q{n}"
    named = {json.load(open(f))["pilot"]: json.load(open(f)).get("slot") for f in glob.glob(f"{q}/*.json")}
    cells = {int(os.path.basename(f)[1:-5]): f for f in glob.glob(f"{q}_slots/s*.json")}
    free_cells = [s for s in cells if s not in named.values() and json.load(open(cells[s]))["frames"]]
    free_pilots = [p["pilot"] for p in off["pilots"] if p["pilot"] not in named]
    if len(free_cells) == 1 and len(free_pilots) == 1:
        J = json.load(open(cells[free_cells[0]])); J["pilot"] = free_pilots[0]
        json.dump(J, open(f"{q}/{free_pilots[0]}.json", "w"))
        print(n, "named", f"s{free_cells[0]}", "->", free_pilots[0])
    elif free_cells or free_pilots:
        print(n, "left: cells", [f"s{s}" for s in free_cells], "pilots", free_pilots)
