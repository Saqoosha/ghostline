"""Build a race page's race.json from each pilot's solved DVR path.
  make_race.py <spec.json> <flights dir> <out race.json>
spec.json (e.g. data/dvr/fdf-2026-r6/race-sf-semifinal/race_spec.json): title, fps, laps, scene, poses (the pose file
in each flight folder), gate (a finish_gate.json), anchor ({pilot, time}: whose official total fixes the start), and
pilots [{name, flight, color, crashed, video, official}].
- Laps: a crossing is the path passing the gate's plane within its width (+1.5 m) and height (+2 m). The first crossing
  is the run through the gate off the start (the holeshot); each later one ends a lap.
- Start (clip time): the anchor pilot's last lap end minus its official total. The others' totals then check the paths.
- Track line: the anchor pilot's laps (holeshot to lap end) averaged by DTW - the mean of the points matched to each line
  point, per lap, then the laps weighted equally, 0.5 m smoothing - three rounds, 1,600 points.
"""
import json, sys
import numpy as np

spec_p, flights, out_p = sys.argv[1:4]
spec = json.load(open(spec_p)); gate = json.load(open(spec["gate"])); fps = spec["fps"]
n = np.array(gate["normal"])[[0, 2]]; w = np.array(gate["width_dir"])[[0, 2]]; c = np.array(gate["centre"])


def crossings(pos):
    rel = pos - c; s = rel[:, [0, 2]] @ n; lat = rel[:, [0, 2]] @ w; h = rel[:, 1]
    return [round((i + s[i] / (s[i] - s[i + 1])) / fps, 3) for i in range(len(pos) - 1)
            if s[i] * s[i + 1] < 0 and abs(lat[i]) < gate["half_width"] + 1.5 and -1 < h[i] < gate["height"] + 2]


paths = {}
for q in spec["pilots"]:
    P = json.load(open(f"{flights}/{q['flight']}/{spec['poses']}"))["poses"]
    last = max(i for i, p in enumerate(P) if p and p["src"] == "ba")      # the path ends at the last frame solved on its own points
    pos = np.array([p["pos"] for p in P[:last + 1]]); paths[q["name"]] = (pos, crossings(pos), last)
L = spec["laps"]; a = spec["anchor"]
start = round(paths[a["pilot"]][1][L] - a["time"], 3)

race = dict(title=spec["title"], fps=fps, start=start, scene=spec["scene"], laps=L, gate=gate, pilots=[])
for q in spec["pilots"]:
    pos, cross, last = paths[q["name"]]; ends = cross[1:1 + L]
    laps = [float(round(b - a_, 3)) for a_, b in zip([start] + ends[:-1], ends)]
    pr = np.round(pos, 2)
    hs = crossings(pr)[0]                                                  # from the published (rounded) path, as the page draws it
    race["pilots"].append(dict(name=q["name"], color=q["color"], end=round(last / fps, 3), crashed=q["crashed"],
                               finish=ends[L - 1] if len(ends) >= L else None, lap_ends=ends, laps=laps,
                               total=round(ends[-1] - start, 3), official=q["official"], pos=pr.tolist(),
                               video=q["video"], holeshot_at=hs, holeshot=round(hs - start, 3)))
    print(f"{q['name']}: laps {laps} total {round(ends[-1] - start, 3)} (official {q['official']['time']}) holeshot {round(hs - start, 3)}")


# --- the track line
k = next(p for p in race["pilots"] if p["name"] == a["pilot"]); pos = np.array(k["pos"]); bounds = [k["holeshot_at"]] + k["lap_ends"][:L]
def at(t): f = t * fps; i = int(f); u = f - i; return pos[i] * (1 - u) + pos[min(i + 1, len(pos) - 1)] * u
def resample(pts, m):
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]; u = np.linspace(0, s[-1], m)
    return np.c_[[np.interp(u, s, pts[:, d]) for d in range(3)]].T
def dtw(A, B, band):
    n_, m = len(A), len(B); D = np.full((n_ + 1, m + 1), np.inf); D[0, 0] = 0
    for i in range(1, n_ + 1):
        cc = int(i * m / n_); lo = max(1, cc - band); hi = min(m, cc + band); d = np.linalg.norm(B[lo - 1:hi] - A[i - 1], axis=1)
        for jj, j in enumerate(range(lo, hi + 1)): D[i, j] = d[jj] + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    i, j = n_, m; pairs = []
    while i > 0 and j > 0:
        pairs.append((i - 1, j - 1)); k_ = np.argmin([D[i - 1, j - 1], D[i - 1, j], D[i, j - 1]])
        if k_ == 0: i, j = i - 1, j - 1
        elif k_ == 1: i -= 1
        else: j -= 1
    return pairs[::-1]
def smooth(x, sig):
    r = int(3 * sig); ww = np.exp(-0.5 * (np.arange(-r, r + 1) / sig) ** 2); ww /= ww.sum()
    xp = np.r_[np.repeat(x[:1], r, 0), x, np.repeat(x[-1:], r, 0)]
    return np.c_[[np.convolve(xp[:, d], ww, 'valid') for d in range(3)]].T
laps = [resample(np.array([at(t) for t in np.linspace(a_, b, int((b - a_) * fps * 8))]), 3200) for a_, b in zip(bounds[:-1], bounds[1:])]
N = 1600; mean = laps[1][::2].copy()
for it in range(3):
    per = []
    for Lp in laps:
        acc = [[] for _ in range(N)]
        for i, j in dtw(mean, Lp, 320): acc[i].append(Lp[j])
        per.append(np.array([np.mean(x, axis=0) for x in acc]))
    mean = resample(smooth(np.mean(per, axis=0), 2), N)
print(f"track line {np.sum(np.linalg.norm(np.diff(mean, axis=0), axis=1)):.1f} m")
race["track"] = np.round(mean, 2).tolist()
json.dump(race, open(out_p, "w"), separators=(",", ":"))
