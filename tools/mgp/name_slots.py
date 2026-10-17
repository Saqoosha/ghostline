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


def score(cross, off):
    """(score_run's score, the run's [first, last] pass, path laps matched).
    A recording can hold two heats (a restart, or the next round recorded on): minutes without a pass split the passes
    into runs, each scored on its own, and the best run is the one the official laps belong to."""
    runs, cur = [], []
    for x in cross:
        if cur and x - cur[-1] > GAP_S:
            runs.append(cur); cur = []
        cur.append(x)
    runs.append(cur)
    best = (9e9, None, 0)
    for r in runs:
        if len(r) >= 2:
            s, n = score_run(np.diff(r).tolist(), off)
            if s < best[0]:
                best = (s, (r[0], r[-1]), n)
    return best


GAP_S = 120                                                # no qualifier lap is this long


LEN_PEN = 0.15                                             # per lap the path has more or fewer than the official list
ONE_LAP_TOL = 0.1                                          # s; one lap alone is weak evidence (Q153: 0.27 s off would have named a stranger)


MERGE = 1.4                                                # a path lap this many times the official median may be 2-3 laps run together


def score_run(laps, off):
    """mean |path lap - official| over the best alignment, plus LEN_PEN per lap left out on either side.
    The path may carry up to 2 extra passes before the race, start late or stop early (fewer laps), and miss a pass
    mid-race: then one path lap is the sum of 2-3 consecutive official laps (only for a lap well over the usual length).
    One lap on its own names a cell only when it is exact (ONE_LAP_TOL)."""
    if not laps or not off:
        return (9e9, 0)
    med = float(np.median(off)); best = [(9e9, 0)]

    def walk(i, j, j0, skip, diffs, merged):
        if diffs and (i == len(laps) or j == len(off)):
            # one lap alone: only an exact, unmerged one (one number fits some sum of 2-3 laps by chance)
            if not (len(diffs) == 1 and (merged or diffs[0] > ONE_LAP_TOL)):
                left = j0 + (len(off) - j) + skip + (len(laps) - i)
                best[0] = min(best[0], (float(np.mean(diffs)) + LEN_PEN * left, len(diffs)))
        if i == len(laps) or j == len(off):
            return
        for g in (1, 2, 3):
            if j + g > len(off) or (g > 1 and laps[i] < MERGE * med):
                continue
            d = abs(laps[i] - sum(off[j:j + g]))
            if d < 3.0:
                walk(i + 1, j + g, j0, skip, diffs + [d], merged or g > 1)

    for skip in range(min(3, len(laps))):
        for j0 in range(len(off)):
            walk(skip, j0, j0, skip, [], False)
    return best[0]                                         # (score, path laps matched)


S = {(cell, p): score(v["cross"], off) for cell, v in cells.items() for p, off in official.items() if off}
os.makedirs(out, exist_ok=True)
for f in os.listdir(out):                                  # a pilot named by an earlier run must not linger
    if f.endswith(".json"):
        os.remove(f"{out}/{f}")
used_c, used_p = set(), set()
def ambiguous(cell, p):
    """a cell named by one lap that another pilot's laps fit as well (Q150: 25.81 s is THE_HERDER's lap 1 and BENNETT's lap 3)."""
    return S[(cell, p)][2] == 1 and any(q != p and v[2] == 1 for (c_, q), v in S.items() if c_ == cell and v[0] <= 1.0)


for (cell, p), (s, run, nl) in sorted(S.items(), key=lambda kv: kv[1][0]):
    if cell in used_c or p in used_p or s > 1.0:
        continue
    if ambiguous(cell, p):
        print(f"{cell}: one lap fits more than one pilot, left unnamed"); used_c.add(cell); continue
    used_c.add(cell); used_p.add(p)
    J = json.load(open(f"{src}/{cells[cell]['file']}")); J["pilot"] = p
    J["run"] = [round(run[0], 3), round(run[1], 3)]       # the passes (video s) its official laps were matched in
    json.dump(J, open(f"{out}/{p}.json", "w"))
    print(f"{cell} -> {p}  (score {s:.2f})  path laps {np.round(cells[cell]['laps'], 2).tolist()}  official {official[p]}")
for cell in cells:
    if cell not in used_c:
        print(f"{cell} -> ?  path laps {np.round(cells[cell]['laps'], 2).tolist()}  best {min((round(s[0], 2), p) for (c_, p), s in S.items() if c_ == cell)}")
for p in official:
    if p not in used_p:
        print(f"   unnamed pilot {p} {official[p]}")
