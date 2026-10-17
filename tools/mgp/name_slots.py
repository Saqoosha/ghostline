"""Which cell is which pilot, from lap times: each cell's path crosses the start/finish gate at some times; the
differences between consecutive crossings are that pilot's laps, and every pilot's official lap sequence (LiveFPV)
is distinctive. The broadcast bar can sit on another cell and the OSD is too small to read, so this is the reliable
way. Writes <out_dir>/<PILOT>.json (the pose file renamed, slot kept) for every cell it could name.
usage: name_slots.py <round> <poses_dir with s<N>.json> <out_dir> [fps=30]"""
import json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import vd_track
from make_mgp_race import crossings, official_for, resample, TRACK_TXT

rnd, src, out = int(sys.argv[1]), sys.argv[2], sys.argv[3]
fps = int(sys.argv[4]) if len(sys.argv) > 4 else 30
gate = vd_track.finish_gate(vd_track.load(TRACK_TXT))
R, _ = official_for(rnd)
official = {p["pilot"]: [t for n, t in p["laps"] if n > 0 and t > 0] for p in R["pilots"]}

cells = {}
for f in sorted(os.listdir(src)):
    if not f.endswith(".json"):
        continue
    J = json.load(open(f"{src}/{f}")); F = J["frames"]
    if len(F) < 10:                                         # a cell that never localized (noisy feed): nothing to name
        print(f"{f[:-5]}: {len(F)} frames, skipped"); continue
    t = np.array([x["t_ms"] for x in F]) / 1000; c = np.array([x["center"] for x in F])
    P = dict(t=t, c=c)
    n = int((t[-1] - t[0]) * fps) + 1
    pos = resample(P, t[0], fps, n)
    cr = [t[0] + x for x in crossings(pos, fps, gate, 1.5, 3.0)]
    cells[f[:-5]] = dict(file=f, laps=np.diff(cr).tolist(), cross=cr, slot=J.get("slot"))


def score(laps, off):
    """mean abs difference between a cell's laps and the official ones, over the overlap; large when nothing lines up."""
    best = 9e9
    for k in range(-2, 3):                                 # the path may miss / add a crossing at the ends
        a = laps[max(0, k):]; b = off[max(0, -k):]
        m = min(len(a), len(b))
        if m >= 1:
            best = min(best, float(np.mean(np.abs(np.array(a[:m]) - np.array(b[:m])))) + 0.5 * abs(len(a) - len(b)))
    return best


S = {(cell, p): score(v["laps"], off) for cell, v in cells.items() for p, off in official.items() if off}
os.makedirs(out, exist_ok=True)
used_c, used_p = set(), set()
for (cell, p), s in sorted(S.items(), key=lambda kv: kv[1]):
    if cell in used_c or p in used_p or s > 1.0:
        continue
    used_c.add(cell); used_p.add(p)
    J = json.load(open(f"{src}/{cells[cell]['file']}")); J["pilot"] = p
    json.dump(J, open(f"{out}/{p}.json", "w"))
    print(f"{cell} -> {p}  (score {s:.2f})  path laps {np.round(cells[cell]['laps'], 2).tolist()}  official {official[p]}")
for cell in cells:
    if cell not in used_c:
        print(f"{cell} -> ?  path laps {np.round(cells[cell]['laps'], 2).tolist()}  best {min((s, p) for (c_, p), s in S.items() if c_ == cell)}")
for p in official:
    if p not in used_p:
        print(f"   unnamed pilot {p} {official[p]}")
